"""Every MCP tool says what it does to the world, or this fails.

The host renders a tool's name, its title and its raw arguments when it asks a
person to approve a call. With no annotations, `graph_status` — which reads a
JSON file — and `snapshot_restore` — which overwrites the working tree — arrived
in that prompt looking alike. Fifty-six tools shipped that way.

`_annotations.meta` falls back to the most cautious hints for a name it does not
know, so the mistake is safe at runtime. This is where it is loud.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from vise.tools import _annotations as ann

TOOLS_DIR = Path(ann.__file__).parent
SERVER = TOOLS_DIR.parent / "server.py"

_REGISTERED = re.compile(r"@_ann\.annotated\(mcp\)\s*\n\s*(?:async\s+)?def\s+(\w+)")
_BARE = re.compile(r"@mcp\.tool\(")


def _sources() -> list[Path]:
    return [p for p in sorted(TOOLS_DIR.glob("*.py")) if p.name != "_annotations.py"] + [SERVER]


def registered_tools() -> set[str]:
    found: set[str] = set()
    for path in _sources():
        found.update(_REGISTERED.findall(path.read_text()))
    return found


def test_every_registered_tool_is_in_the_table():
    missing = registered_tools() - set(ann.TOOLS)
    assert not missing, (
        f"no annotation for {sorted(missing)} — they would reach the approval "
        f"prompt as destructive/open-world, which is safe but wrong"
    )


def test_the_table_describes_no_tool_that_does_not_exist():
    """A stale entry is a promise about a tool nobody can call."""
    extra = set(ann.TOOLS) - registered_tools()
    assert not extra, f"table describes tools that are not registered: {sorted(extra)}"


def test_no_tool_is_registered_without_going_through_the_table():
    """`@mcp.tool(...)` bypasses `annotated`, and a bypass is how the next
    fifty-six start."""
    offenders = [p.name for p in _sources() if _BARE.search(p.read_text())]
    assert not offenders, f"bare tool decorator in {offenders}; use @_ann.annotated(mcp)"


@pytest.mark.parametrize("name", sorted(ann.TOOLS))
def test_every_entry_carries_all_four_hints_and_a_title(name):
    title, hints = ann.TOOLS[name]
    assert title and title != name, f"{name}: needs a title a person can read"
    assert set(hints) == {
        "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint",
    }, f"{name}: {sorted(hints)}"
    assert all(isinstance(v, bool) for v in hints.values())


@pytest.mark.parametrize("name", sorted(ann.TOOLS))
def test_a_read_only_tool_is_never_destructive(name):
    """`destructiveHint` is only meaningful when the tool writes at all, and the
    two together describe nothing a host can act on."""
    _, hints = ann.TOOLS[name]
    if hints["readOnlyHint"]:
        assert not hints["destructiveHint"], f"{name} claims to both read only and destroy"


@pytest.mark.parametrize("name", [
    "snapshot_restore",   # overwrites the working tree
    "graph_reset",        # drops the workflow's state
    "goal_clear",         # drops the active goal
    "graph_builder_delete",
])
def test_the_tools_that_destroy_say_so(name):
    """Pinned by name. These are the four a person most needs the prompt to
    distinguish from a read, and a refactor that silently reclassifies one of
    them should fail here rather than in someone's working tree."""
    title, hints = ann.TOOLS[name]
    assert hints["destructiveHint"], f"{name} must be marked destructive"
    assert not hints["readOnlyHint"]
    assert any(w in title.lower() for w in ("discard", "overwrite", "delete", "clear", "reset")), (
        f"{name}: the title is what the approval prompt shows — say what it destroys"
    )


def test_querying_experience_is_not_read_only():
    """It bumps FSRS stability and `last_reviewed` on everything it returns and
    saves the store. A `# readOnlyHint: True` comment sat above it for releases
    saying otherwise — reading this memory is how it learns what to keep."""
    _, hints = ann.TOOLS["experience_query"]
    assert not hints["readOnlyHint"]


def test_the_unknown_fallback_warns_about_everything():
    meta = ann.meta("a_tool_that_does_not_exist")
    assert meta["annotations"] == ann.UNKNOWN
    assert meta["annotations"]["destructiveHint"] is True
    assert meta["annotations"]["openWorldHint"] is True
