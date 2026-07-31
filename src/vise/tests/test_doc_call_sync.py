"""vise's own prose must call vise's own MCP tools with real parameter names.

Four of these shipped and were fixed by hand, one at a time, each only found
by a human reading the call site next to the real signature:

  1. ``src/vise/tools/_graph_query.py`` — a hint string told a model to call
     ``graph_activate(name=...)``; the real parameter is ``graph_name``.
  2. ``src/vise/assets/workflows/feature-dev-graph.yaml`` — a node prompt told
     a model to call ``capability_audit(capability=...)``; the tool takes no
     parameters at all.
  3. ``README.md`` — the rollback recipe called ``snapshot_restore(snap_id=
     ...)``; the real parameter is ``snapshot_id``.
  4. ``src/vise/cli/graph_cmd.py`` — a generated prompt told a model to call
     ``graph_traverse(direction='next')``; the real parameter is ``edge_id``.

A model that follows any of these gets ``TypeError: unexpected keyword
argument``. Being hand-fixed four times means it can regress by hand a fifth
time; this test makes that regression fail CI instead of a user's session.

MECHANISM: register every real tool through a fake MCP whose ``.tool()``
decorator just records the function (same capture shape as
test_graph_query_tools.py / test_node_gate_traverse.py), then
``inspect.signature`` each one for its real parameter names. Scan README.md,
agents/*.md, skills/*/SKILL.md, commands/*.md, the workflow YAMLs, the recipe
YAMLs, and the package's own .py files (excluding this test package — tests legitimately
construct calls that are *meant* to be wrong, to assert they fail) for
``tool_name(...)`` call sites, using a paren-depth walk (not a single-line
regex) so a call that wraps across lines is read whole instead of silently
truncated at the first newline. Each call's arguments are then split only on
TOP-LEVEL commas (respecting nested parens/brackets/braces and quoted
strings), so a kwarg inside a *nested* call — ``outer(x=inner(y=1))`` — is
never mistaken for a kwarg of the outer, documented tool.

BLIND SPOTS — this gate only sees literal ``kwarg=`` forms inside a call to a
known tool name, and it is not trying to be more than that:

  - It does NOT catch prose that names a nonexistent concept without ever
    writing a call. Example that shipped: the ``agent-autoheal`` skill told
    writers to record memory "with tag ``agent:<name>``" — no experience tool
    has a ``tags`` parameter — but nothing there looks like ``fn(tags=...)``,
    so no regex over call syntax will ever see it.
  - It does NOT catch a docstring whose VERB is wrong while its signature is
    right. ``recipe_run`` was once documented as "Execute" a recipe when it
    actually returns a plan for something else to execute; the parameter list
    was fine, so this gate would stay green through that bug.
  - It does NOT catch a deliberately-wrong example inside a code fence that
    is *teaching* the wrong call (e.g. "don't write `tool(bad_kwarg=...)`").
    There are none of those in this repo today (see the empty
    ``ALLOWED_MISMATCHES`` below); if one is ever added, it must go in that
    dict and nowhere else — a pre-populated escape hatch is where real drift
    hides.
  - It does NOT check an unterminated call — a line naming ``tool(`` with no
    matching close paren anywhere in the file. Rather than let unrelated
    ``key=value`` text later in the file get vacuumed in as fake arguments of
    that call, such a site is skipped outright (see ``depth != 0`` below).

A gate this size can also go quietly green for the wrong reason: an empty
scan, or a tool missing from the captured registry, both "pass" with zero
findings. ``test_scan_actually_scans_something`` exists so that failure mode
fails loudly instead of silently, the same way test_tool_surface_sync.py
insists on a real, non-empty registry rather than trusting an empty one by
default.

Sibling of test_env_var_docs_sync.py and test_no_phantom_imports.py.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]

# Genuine exceptions only: a call site that *deliberately* shows a bogus
# kwarg (e.g. as a counter-example) rather than one nobody has noticed yet.
# Keyed by (relative file path, tool name, bogus kwarg); value is a caveat
# string that must appear on the same line as the call, so the file itself
# admits the example is intentionally wrong.
#
# Currently EMPTY, and that is the point — every entry here is one fewer real
# bug this test can catch. If the scan below ever finds something, the fix is
# almost always to correct the prose, not to grow this dict.
ALLOWED_MISMATCHES: dict[tuple[str, str, str], str] = {}


def _real_signatures() -> dict[str, set[str]]:
    """Every registered tool's name mapped to its real parameter names."""

    class _FakeMCP:
        def __init__(self) -> None:
            self.tools: dict[str, Any] = {}

        def tool(self, *_a: Any, **_kw: Any):
            def _decorator(fn: Any) -> Any:
                self.tools[fn.__name__] = fn
                return fn

            return _decorator

    from vise.server import vise_version
    from vise.tools.bootstrap import register_all

    fake = _FakeMCP()
    register_all(fake)
    # register_all only wires the tool FAMILIES (graph, experience, snapshot,
    # recipes, goal). vise_version is decorated directly on the real server's
    # `mcp` in server.py and never passes through register_all — capturing it
    # separately here is the only way it lands in the signature map at all.
    fake.tools["vise_version"] = vise_version
    sigs = {name: set(inspect.signature(fn).parameters) for name, fn in fake.tools.items()}
    return sigs


def _scanned_files() -> list[Path]:
    globs = [
        "README.md",
        "agents/*.md",
        "skills/*/SKILL.md",
        "commands/*.md",
        "src/vise/assets/workflows/*.yaml",
        "src/vise/assets/recipes/*.yaml",
    ]
    files = [p for g in globs for p in REPO_ROOT.glob(g)]
    files += [
        p for p in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    ]
    return sorted(set(files))


def _split_top_level_args(argstr: str) -> list[str]:
    """Split call arguments on commas that are not nested inside
    ``()``/``[]``/``{}`` or a quoted string, so a kwarg belonging to a nested
    call is never split apart from (or confused with) the outer call's own
    arguments.
    """
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(argstr):
        ch = argstr[i]
        if quote:
            current.append(ch)
            if ch == quote and argstr[i - 1] != "\\":
                quote = None
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        args.append("".join(current))
    return args


_TOP_LEVEL_KWARG = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*=(?!=)")


def _calls_in(text: str, tool_names: set[str]) -> list[tuple[str, str, int]]:
    """Return ``(tool, kwarg, line)`` for every bogus-looking call site.

    Walks paren depth from each ``tool_name(`` occurrence to its matching
    close paren, so a call that wraps across multiple lines is read whole
    instead of being truncated (or misread) at the first newline.
    """
    if not tool_names:
        return []
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(tool_names)) + r")\s*\(")
    found: list[tuple[str, str, int]] = []
    for m in pattern.finditer(text):
        tool = m.group(1)
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        if depth != 0:
            # Never found a matching close paren (e.g. prose that trails off
            # mid-sentence after "tool("). Treating the rest of the file as
            # this call's argument string would vacuum in unrelated
            # `key=value` text far below and misreport it as a bogus kwarg —
            # skip the site instead of guessing.
            continue
        argstr = text[start : i - 1]
        line = text.count("\n", 0, m.start()) + 1
        for arg in _split_top_level_args(argstr):
            kw_match = _TOP_LEVEL_KWARG.match(arg)
            if kw_match:
                found.append((tool, kw_match.group(1), line))
    return found


# The four historical bugs this test exists to catch, by tool name — used
# below only as a floor check that the registry capture actually found them,
# not as the set of things scanned for (every tool's every kwarg is checked).
_HISTORICALLY_BUGGY_TOOLS = {"graph_activate", "capability_audit", "snapshot_restore", "graph_traverse"}


def test_scan_actually_scans_something() -> None:
    """A gate that silently scans zero files or zero tools is worse than no
    gate: it reports green forever while catching nothing. This would be the
    failure mode of, e.g., REPO_ROOT resolving wrong after a repo reshuffle,
    or a tool family silently dropping out of register_all.
    """
    sigs = _real_signatures()
    assert _HISTORICALLY_BUGGY_TOOLS <= set(sigs), (
        "the tool registry capture is missing one of the tools this test's "
        f"own history was written to guard: missing "
        f"{_HISTORICALLY_BUGGY_TOOLS - set(sigs)}"
    )
    assert "vise_version" in sigs, "vise_version dropped out of the signature capture"

    files = _scanned_files()
    assert files, "the scan globs matched zero files — REPO_ROOT or the glob list is wrong"
    required = {
        REPO_ROOT / "README.md",
        REPO_ROOT / "src/vise/assets/workflows/feature-dev-graph.yaml",
        REPO_ROOT / "src/vise/cli/graph_cmd.py",
    }
    missing = required - set(files)
    assert not missing, f"required files dropped out of the scan set: {sorted(missing)}"


def test_documented_tool_calls_use_real_parameter_names() -> None:
    sigs = _real_signatures()
    tool_names = set(sigs)
    findings: list[str] = []

    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(REPO_ROOT))
        for tool, kwarg, line in _calls_in(text, tool_names):
            if kwarg in sigs[tool]:
                continue
            if (rel, tool, kwarg) in ALLOWED_MISMATCHES:
                continue
            findings.append(
                f"{rel}:{line}  {tool}({kwarg}=...) — not a real parameter. "
                f"real parameters of {tool}: {sorted(sigs[tool])}"
            )

    assert not findings, (
        "prose calls a real vise tool with a keyword argument that does not "
        "exist — a model following this literally gets TypeError:\n  "
        + "\n  ".join(findings)
    )


def test_allowlisted_mismatches_carry_their_caveat() -> None:
    """The escape hatch above only holds while the file admits the caveat.

    Mirrors test_not_wired_knobs_are_labelled_as_such_in_the_readme in
    test_env_var_docs_sync.py: trivially green while ALLOWED_MISMATCHES is
    empty (the current, intended state), and it stops a future entry from
    silently hiding a real bug behind an unexplained allowlist key.
    """
    for (rel, tool, kwarg), caveat in ALLOWED_MISMATCHES.items():
        path = REPO_ROOT / rel
        assert path.is_file(), f"{rel} is allowlisted but no longer exists"
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        matches = [
            ln for ln in lines
            if f"{tool}(" in ln and f"{kwarg}=" in ln
        ]
        assert matches, (
            f"{rel} is allowlisted for {tool}({kwarg}=...) but no line in the "
            "file still contains that call"
        )
        assert any(caveat in ln for ln in matches), (
            f"{rel}'s {tool}({kwarg}=...) call must carry the caveat "
            f"{caveat!r} on the same line so readers are not told to use a "
            f"kwarg that does not exist. Line(s): {matches}"
        )
