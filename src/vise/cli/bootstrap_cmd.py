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
import shutil
from pathlib import Path
from typing import Any

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
    "node": {
        "unit": [["npm", "test", "--silent"]],
        "lint": [["npx", "--no-install", "eslint", "."]],
        "types": [["npx", "--no-install", "tsc", "--noEmit"]],
        "sca": [["npm", "audit", "--audit-level=high"]],
    },
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

    pools = [_CANDIDATES.get(e, {}) for e in ecosystems] + [_UNIVERSAL]
    for pool in pools:
        for check, options in pool.items():
            if check in bound:
                continue
            for cmd in options:
                if _resolves(project, cmd):
                    bound[check] = cmd
                    break
            else:
                names = [_tool_name(c) for c in options]
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


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    project = Path(args.project_dir or ".").expanduser().resolve()
    target = project / ".vise" / "quality.yaml"

    found = detect(project)

    if target.exists() and not args.force:
        print(f"{target} already exists — not overwriting. Re-run with --force.")
        print("\nWhat detection would have written:\n")
        print(render(found))
        return 0

    if args.dry_run:
        print(render(found))
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(found), encoding="utf-8")
    print(f"wrote {target}")
    print(f"  bound:   {', '.join(sorted(found['bound'])) or '(nothing — no tools found)'}")
    if found["skipped"]:
        print(f"  skipped: {', '.join(sorted(found['skipped']))}")

    unit = found["bound"].get("unit")
    lint = found["bound"].get("lint")
    if unit or lint:
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


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "bootstrap",
        help="write .vise/quality.yaml for this repo, binding only tools that exist",
    )
    p.add_argument("--project-dir", default=None, help="defaults to the cwd")
    p.add_argument("--dry-run", action="store_true", help="print, do not write")
    p.add_argument("--force", action="store_true", help="overwrite an existing profile")
    p.set_defaults(func=_cmd_bootstrap)
