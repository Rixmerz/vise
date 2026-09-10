"""`vise bootstrap` — write this repo's quality profile, and nothing it cannot run.

Installing the plugin ships the agents, skills, commands and hooks. What it
cannot ship is the part that is *about this repo*: which command runs the tests
here, which one lints, what `sast` means in a Go project. Those live in
`.vise/quality.yaml` and in two environment variables, and until now every
project wrote them by hand — which mostly meant not writing them, so the gates
skip-passed and the enforcement people installed vise for never ran.

The rule that shapes the whole file: **bind a check only when its tool is
actually present.** A profile that names `pytest` in a repo without pytest does
not create rigour, it creates a gate that fails for environment reasons — and a
gate that goes red when you did nothing wrong is how a team learns to export
`VISE_NODE_GATE_OVERRIDE=1`, which is the one habit the gates exist to prevent.
Every unbound check skip-passes with `source="asserted"` and says so, which is
an honest "nobody looked" rather than a false green.

Detection is deliberately shallow: manifests on disk plus `shutil.which`. No
parsing of CI config, no guessing at monorepo layouts. A wrong guess here is
worse than an absence, because an absence is visible in the generated file and
a wrong guess looks like a decision someone made.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from vise.core import consent
from vise.core.atomic import write_atomic
from vise.cli._browser_probe import browser_status_quiet

# (manifest, ecosystem). Order matters only for reporting; a polyglot repo
# matches several and gets checks for each.
_MANIFESTS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements.txt", "python"),
    ("package.json", "node"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
)

# check name -> ordered candidates. First one whose binary resolves wins.
# `venv` entries come first for Python because a bare `pytest` resolves against
# whatever PATH the MCP server inherited, not the project's — the failure this
# repo's own quality.yaml documents at length.
_CANDIDATES: dict[str, dict[str, list[list[str]]]] = {
    "python": {
        "unit": [[".venv/bin/python", "-m", "pytest", "-q"], ["pytest", "-q"]],
        "lint": [[".venv/bin/python", "-m", "ruff", "check", "."], ["ruff", "check", "."]],
        "types": [[".venv/bin/python", "-m", "mypy", "."], ["mypy", "."]],
        "sast": [[".venv/bin/python", "-m", "bandit", "-q", "-r", ".", "--severity-level", "medium"]],
        "sca": [[".venv/bin/python", "-m", "pip_audit"]],
    },
    # `node` is not here: its candidates are read off package.json by
    # `_node_candidates`, because a static table cannot know the package
    # manager or the script names.
    "go": {
        "unit": [["go", "test", "./..."]],
        "lint": [["golangci-lint", "run"], ["go", "vet", "./..."]],
        "sast": [["gosec", "./..."]],
    },
    "rust": {
        "unit": [["cargo", "test"]],
        "lint": [["cargo", "clippy", "--", "-D", "warnings"]],
        "sca": [["cargo", "audit"]],
    },
    "java": {"unit": [["mvn", "-q", "test"], ["gradle", "test"]]},
    "ruby": {"unit": [["bundle", "exec", "rspec"]], "lint": [["bundle", "exec", "rubocop"]]},
    "php": {"unit": [["vendor/bin/phpunit"]], "lint": [["vendor/bin/phpcs"]]},
}

# A few tools are opt-in: having the binary installed says nothing about whether
# THIS repo adopted them. `mypy` is on PATH in most Python environments, and
# `mypy .` on a repo that never configured it produces an avalanche of errors —
# a gate red for missing configuration rather than for broken code, which is the
# same false-red this whole module is built to avoid.
#
# Keyed by TOOL, not by check, and consulted per candidate. Keying it by check
# was the first version and it broke the fallbacks: Go's `lint` fell back to
# `go vet ./...` and Rust's to `cargo clippy`, both of which are designed to run
# configless, but blocking the check blocked those too — so two ecosystems lost
# a linter they could actually have used. Only tools that are genuinely noisy
# without configuration belong here.
#
# `(filename, marker)` — marker None means the file's existence is enough,
# otherwise the marker has to appear inside it.
_NEEDS_CONFIG: dict[str, list[tuple[str, str | None]]] = {
    "mypy": [
        ("mypy.ini", None), (".mypy.ini", None),
        ("pyproject.toml", "[tool.mypy]"), ("setup.cfg", "[mypy]"),
    ],
    "pyright": [("pyrightconfig.json", None), ("pyproject.toml", "[tool.pyright]")],
    "eslint": [
        (".eslintrc", None), (".eslintrc.json", None), (".eslintrc.js", None),
        ("eslint.config.js", None), ("eslint.config.mjs", None),
        ("package.json", "eslintConfig"),
    ],
    "golangci-lint": [(".golangci.yml", None), (".golangci.yaml", None)],
    "rubocop": [(".rubocop.yml", None)],
}


#: Node script names that mean a check, most conventional first. Read off
#: `package.json` rather than guessed: the repo already wrote the answer there,
#: and a `scripts.lint` entry is the project saying "this is how you lint me",
#: which is stronger evidence than a linter config file on disk.
_NODE_SCRIPTS: dict[str, tuple[str, ...]] = {
    "unit": ("test", "test:unit"),
    "lint": ("lint",),
    "types": ("typecheck", "type-check", "tsc"),
}

#: Lockfile -> package manager, consulted when `packageManager` is absent.
_LOCKFILES: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
)

#: What is missing when a pool offers no candidate *at all* for a check, as
#: opposed to offering one whose tool is absent. Only the node pool is computed
#: rather than tabulated, so only it can be empty — but the message is phrased
#: for a reader, not for an ecosystem, and it is rendered as "no <value>".
_ABSENT: dict[str, str] = {
    "unit": "test script in package.json",
    "lint": "lint script in package.json",
    "types": "typecheck script in package.json",
    "sca": "audit command for this package manager",
}


def _read_json(path: Path) -> dict[str, Any]:
    """A JSON object off disk, or an empty one. Never raises."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _package_manager(project: Path) -> str:
    """Which package manager runs this repo's scripts.

    `npm test` in a pnpm workspace is not a small inaccuracy: it resolves
    against a different store and a different workspace protocol, so the bound
    command either fails or runs something else. Order is the repo's own
    declaration first (`packageManager`, which corepack reads), then the
    lockfile, then npm — each one a fact on disk rather than a guess.
    """
    declared = str(_read_json(project / "package.json").get("packageManager") or "")
    name = declared.split("@", 1)[0].strip()
    if name in ("pnpm", "yarn", "bun", "npm"):
        return name
    for filename, pm in _LOCKFILES:
        if (project / filename).exists():
            return pm
    return "npm"


def _node_candidates(project: Path) -> dict[str, list[list[str]]]:
    """Node's checks, read off `package.json` before falling back to a binary.

    The static table this replaced bound `unit` to `npm test --silent` on the
    strength of `shutil.which("npm")` alone. npm is on PATH wherever Node is,
    so that bound *always*: in the wrong package manager on a pnpm or yarn
    repo, and on repos with no test script at all, where `npm test` fails for
    having nothing to run. `npm audit` had the same problem in a pnpm
    workspace. Detection never opened `package.json`, which is where the answer
    was the whole time.
    """
    pm = _package_manager(project)
    scripts = _read_json(project / "package.json").get("scripts")
    if not isinstance(scripts, dict):
        scripts = {}

    out: dict[str, list[list[str]]] = {
        check: [[pm, "run", name] for name in names if name in scripts]
        for check, names in _NODE_SCRIPTS.items()
    }
    # A binary the repo has locally, for a check its scripts do not name. There
    # is no equivalent for `unit`: a test runner with no script behind it is a
    # guess about arguments, and `npm test` on a repo with no test script is the
    # false bind above.
    out["lint"].append(["npx", "--no-install", "eslint", "."])
    out["types"].append(["npx", "--no-install", "tsc", "--noEmit"])
    # `npm audit` and `pnpm audit` take this flag; yarn and bun do not, and a
    # wrong invocation reads as "this repo has no SCA" rather than as the
    # mistake it is — so they get no candidate, and say so.
    out["sca"] = [[pm, "audit", "--audit-level=high"]] if pm in ("npm", "pnpm") else []
    return out


def _tool_name(cmd: list[str]) -> str:
    """The tool a reader would recognise, not the interpreter that launches it.

    `[".venv/bin/python", "-m", "mypy", "."]` is mypy. Reporting the head made
    the skip line read "no .venv/bin/python" on a repo whose venv plainly
    exists — a message that sends someone looking in the wrong place.
    """
    if len(cmd) >= 3 and cmd[1] == "-m":
        return cmd[2]
    if cmd[0] == "npx" and len(cmd) > 2:
        return cmd[2]
    return cmd[0]


def _configured(project: Path, tool: str) -> bool:
    """Did this repo opt into the tool, or is it merely installed on the box?"""
    evidence = _NEEDS_CONFIG.get(tool)
    if evidence is None:
        return True
    for filename, marker in evidence:
        fp = project / filename
        if not fp.exists():
            continue
        if marker is None:
            return True
        try:
            if marker in fp.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


# Language-agnostic, bound whenever the binary is there.
#
# `secrets` leads with the venv form for the same reason the Python block does:
# detect-secrets is a Python package, so on a repo that followed vise's own
# setup it lives in `.venv/bin` and never reaches PATH. Probing PATH alone
# reported "no detect-secrets" against a repo that had it installed and
# working — under-detection is worse than the false bind above, because the
# user reads it as a gap and knowingly accepts a hole that is not there.
_UNIVERSAL: dict[str, list[list[str]]] = {
    "secrets": [
        [".venv/bin/python", "-m", "detect_secrets", "scan"],
        ["detect-secrets", "scan"],
        ["gitleaks", "detect", "--no-banner"],
    ],
    "spec": [["openspec", "validate", "--all", "--strict"]],
}


def _resolves(project: Path, cmd: list[str]) -> bool:
    """Is this command actually runnable in this repo?

    A relative path (`.venv/bin/python`, `vendor/bin/phpunit`) is checked on
    disk; anything else against PATH. `npx --no-install` counts as present only
    when the local package is there, because plain `npx` would happily download
    a linter mid-gate — a gate that installs software is not a gate.

    The `python -m <module>` form needs the MODULE checked, not the
    interpreter. Checking only the head bound `types: mypy` on this very repo,
    which has no mypy: the interpreter existed, so the check looked available
    and would have failed at gate time for a missing tool. That is precisely
    the false bind this module exists to avoid, and it took a dry run against a
    real repo to see it — the head-only version passed every unit test I had
    written for it.
    """
    if not _configured(project, _tool_name(cmd)):
        return False

    head = cmd[0]

    if len(cmd) >= 3 and cmd[1] == "-m":
        interpreter = project / head if "/" in head else Path(shutil.which(head) or "")
        if not interpreter.exists():
            return False
        return _module_importable(interpreter, cmd[2])

    if "/" in head:
        return (project / head).exists()
    if head == "npx":
        target = cmd[2] if len(cmd) > 2 else ""
        return (project / "node_modules" / ".bin" / target).exists()
    return shutil.which(head) is not None


def _module_importable(interpreter: Path, module: str) -> bool:
    """Can that interpreter import that module?

    Uses `importlib.util.find_spec`, which locates without executing — running
    `import bandit` for real would pay each candidate's import cost, and
    `python -m mypy --version` would pay a process per probe.
    """
    import subprocess

    probe = (
        "import importlib.util,sys; "
        f"sys.exit(0 if importlib.util.find_spec({module!r}) else 1)"
    )
    try:
        return subprocess.run(
            [str(interpreter), "-c", probe],
            capture_output=True, timeout=10, check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect(project: Path) -> dict[str, Any]:
    """What this repo is, and what of it can actually be checked."""
    ecosystems = sorted({
        eco for manifest, eco in _MANIFESTS if (project / manifest).exists()
    })

    bound: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}

    pools = [
        _node_candidates(project) if e == "node" else _CANDIDATES.get(e, {})
        for e in ecosystems
    ] + [_UNIVERSAL]
    for pool in pools:
        for check, options in pool.items():
            if check in bound:
                continue
            for cmd in options:
                if _resolves(project, cmd):
                    bound[check] = cmd
                    break
            else:
                # An empty option list is a check this repo offers no way to
                # run at all. It still has to appear under "Not bound": a gap
                # nobody can see is one nobody accepts knowingly.
                names = [_tool_name(c) for c in options] or [
                    _ABSENT.get(check, "any runnable command")]
                needs = [n for n in names if n in _NEEDS_CONFIG]
                skipped.setdefault(
                    check,
                    f"{names[0]} config (installed, but this repo has none)"
                    if needs and shutil.which(needs[0]) else names[0],
                )

    return {
        "ecosystems": ecosystems,
        "bound": bound,
        "skipped": {k: v for k, v in skipped.items() if k not in bound},
    }


def render(found: dict[str, Any]) -> str:
    eco = ", ".join(found["ecosystems"]) or "unrecognised"
    lines = [
        "# vise quality profile — generated by `vise bootstrap`, then edited by you.",
        "#",
        f"# Detected: {eco}.",
        "#",
        "# A workflow node gates on a check NAME; this file says what that name runs",
        "# HERE. Only checks whose tool was actually found are bound: naming a tool",
        "# this repo does not have would make the gate fail for environment reasons,",
        "# and a gate that goes red when you did nothing wrong teaches people to set",
        "# VISE_NODE_GATE_OVERRIDE=1. An unbound check skip-passes and says so, which",
        "# is an honest \"nobody looked\" rather than a false green.",
        "",
        "checks:",
    ]
    for name, cmd in sorted(found["bound"].items()):
        rendered = ", ".join(f'"{part}"' for part in cmd)
        lines.append(f"  {name}: [{rendered}]")

    if found["skipped"]:
        lines += [
            "",
            "# Not bound — the tool was not found. Each one is a real gap, not an",
            "# oversight: install the tool and add the line, or accept the risk",
            "# knowingly. They skip-pass until then.",
        ]
        for name, tool in sorted(found["skipped"].items()):
            lines.append(f"#   {name:<10} — no {tool}")
    return "\n".join(lines) + "\n"


def _design_gates_report() -> str:
    """The three design gates fail closed without a browser. Say so here.

    CI never installed one, and nobody knew until a gate asked. Bootstrap is
    where a person is already looking at what this repo can and cannot check,
    so the answer belongs here — not in the first ``ui_layout`` block a week
    later, after bootstrap said the repo was ready.
    """
    ok, reason = browser_status_quiet()
    if ok:
        return "\ndesign gates (ui_layout, ui_contrast): browser found — they will run."
    return (
        "\ndesign gates (ui_layout, ui_contrast): NO BROWSER — they fail closed "
        f"until one exists.\n  {reason}"
    )


def _neighbours_report(project: Path) -> str:
    """What the servers vise runs beside left in this repo, and what follows.

    Bootstrap is where someone is already looking at what this repo can and
    cannot check, and two of the three facts here change how vise behaves:
    without a livespec index the CodeLayer gate stands down and `symbol_index`
    refuses, and a Graphify graph puts files in every diff that nobody edited.
    Both are cheaper to learn here than in the first red gate a week later.
    """
    from vise.core.neighbour_state import GRAPHIFY_GRAPH, graph_state, index_state

    lines = ["\nneighbouring servers (vise names them and cannot call them):"]
    index = index_state(project)
    lines.append(f"  livespec  {index.detail}")
    if index.refuses:
        lines.append(
            "            -> the CodeLayer gate stands down and `symbol_index` "
            "fails closed until\n"
            "               livespec indexes this repo. Nothing else is affected."
        )
    graph = graph_state(project)
    if graph.present:
        lines.append(f"  Graphify  {graph.detail}")
        lines.append(
            f"            -> `{GRAPHIFY_GRAPH}` is committed by convention and "
            "rebuilt by a git\n"
            "               post-commit hook, so it lands in diffs nobody "
            "edited. If you wire the\n"
            "               `diff_scope` gate, put `graphify-out/**` in its "
            "`allow` list or it goes\n"
            "               red on a file no human touched."
        )
    lines.append("  (`vise neighbours` reports this any time, with more detail)")
    return "\n".join(lines)


#: The gate validator that reads each variable, and the profile check whose
#: bound command answers it. `tests_pass` and `lint_pass` do not read
#: `.vise/quality.yaml` — they read the environment — so the same fact has to
#: reach both places or the profile is right and the gate still skip-passes.
_ENV_FOR_CHECK: tuple[tuple[str, str], ...] = (
    ("VISE_TEST_CMD", "unit"),
    ("VISE_LINT_CMD", "lint"),
)


def write_settings_env(
    project: Path, bound: dict[str, list[str]]
) -> tuple[dict[str, str], dict[str, str], str]:
    """Put the two gate commands into `.claude/settings.json`, and nothing else.

    Returns ``(written, kept, error)``. ``kept`` holds keys that were already
    there and were left exactly as they were.

    Bootstrap used to print these and tell the person to paste them, on the
    reasoning that `.claude/settings.json` is theirs and holds things vise has
    no business touching. The second half of that is true and this keeps it: an
    existing value is never replaced, unparseable JSON is never overwritten, no
    other key is read or written, and the write is atomic. The first half was
    the mistake. Detection has already computed the command; printing it and
    hoping mostly meant it never got pasted, and the consequence of not pasting
    it is a `tests_pass` gate that runs `pytest` on a Node repo, reports
    `unverified`, and reads as green. A step that must be done by hand for the
    tool to be honest is a step the tool should do.
    """
    written: dict[str, str] = {}
    kept: dict[str, str] = {}
    wanted = {
        key: " ".join(bound[check])
        for key, check in _ENV_FOR_CHECK
        if bound.get(check)
    }
    if not wanted:
        return written, kept, ""

    path = project / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return written, kept, f"{path} is not readable JSON ({exc}); left alone"
        if not isinstance(raw, dict):
            return written, kept, f"{path} is not a JSON object; left alone"
        settings = raw

    env = settings.get("env", {})
    if not isinstance(env, dict):
        return written, kept, f'{path} has a non-object "env"; left alone'

    for key, value in wanted.items():
        if key in env:
            kept[key] = str(env[key])
        else:
            env[key] = value
            written[key] = value

    if not written:
        return written, kept, ""

    settings["env"] = env
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {}, kept, f"could not write {path} ({exc})"
    return written, kept, ""


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    project = Path(args.project_dir or ".").expanduser().resolve()
    target = project / ".vise" / "quality.yaml"

    found = detect(project)

    if target.exists() and not args.force:
        print(f"{target} already exists — not overwriting. Re-run with --force.")
        print("\nWhat detection would have written:\n")
        print(render(found))
        print(_design_gates_report())
        print(_neighbours_report(project))
        return 0

    if args.dry_run:
        print(render(found))
        print(_design_gates_report())
        print(_neighbours_report(project))
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(found), encoding="utf-8")
    print(f"wrote {target}")
    print(f"  bound:   {', '.join(sorted(found['bound'])) or '(nothing — no tools found)'}")
    if found["skipped"]:
        print(f"  skipped: {', '.join(sorted(found['skipped']))}")

    # The person is here, running bootstrap: that is the consent a profile
    # that arrived with a clone never had. See vise.core.consent.
    for check, cmd in sorted(found["bound"].items()):
        consent.approve(project, check, cmd)
    if found["bound"]:
        print(
            f"  approved: {len(found['bound'])} check(s) to run at the node gate on this "
            "machine (a cloned profile needs `vise approve`)"
        )

    print(_design_gates_report())
    print(_neighbours_report(project))

    unit = found["bound"].get("unit")
    lint = found["bound"].get("lint")
    if not (unit or lint):
        return 0

    if getattr(args, "no_settings", False):
        print("\nAdd to .claude/settings.json under \"env\" so the node-gate")
        print("validators run this repo's commands instead of their defaults:")
        print("")
        if unit:
            print(f'    "VISE_TEST_CMD": "{" ".join(unit)}",')
        if lint:
            print(f'    "VISE_LINT_CMD": "{" ".join(lint)}"')
        print("")
        print("Without those two, tests_pass and lint_pass report `unverified`:")
        print("the gate exists but does not bite.")
        return 0

    written, kept, error = write_settings_env(project, found["bound"])
    settings_path = project / ".claude" / "settings.json"
    if error:
        print(f"\ncould not set the gate variables: {error}")
        print("Add them yourself under \"env\", or the node-gate validators keep")
        print("their defaults and report `unverified` — the gate exists but does")
        print("not bite:")
        for key, check in _ENV_FOR_CHECK:
            if found["bound"].get(check):
                print(f'    "{key}": "{" ".join(found["bound"][check])}"')
        return 0

    for key, value in sorted(written.items()):
        print(f'\nset {key}="{value}" in {settings_path}')
    for key, value in sorted(kept.items()):
        print(f'\nkept {key}="{value}" as it was in {settings_path} — '
              f"vise never replaces one you set")
    if not written and not kept:
        print("\nno gate variables to set")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "bootstrap",
        help="write .vise/quality.yaml for this repo, binding only tools that exist",
    )
    p.add_argument("--project-dir", default=None, help="defaults to the cwd")
    p.add_argument("--dry-run", action="store_true", help="print, do not write")
    p.add_argument("--force", action="store_true", help="overwrite an existing profile")
    p.add_argument(
        "--no-settings", action="store_true",
        help="print the gate variables instead of setting them in .claude/settings.json",
    )
    p.set_defaults(func=_cmd_bootstrap)
