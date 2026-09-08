"""The baseline tells every agent to check a library's API; it names no vendor.

All 22 charters preload ``engineering-baseline``, so a rule added there reaches
every code-touching agent at once. That leverage cuts both ways: a tool name
written here is taught 22 times and checked nowhere, because a third party's
MCP server is not in this repository and its renames arrive without notice.

``core/neighbours.py`` records what that costs. vise pinned ``locate`` and
``compute_index_status`` — one that never existed, one removed in livespec v0.9
— and shipped both into the surfaces most dependent on being obeyed. Those were
names from a *neighbour repository the author could read*. A hosted
documentation service is further away than that, not closer: at the time this
rule was written, the tool the ecosystem calls ``get-library-docs`` in every
guide had already been renamed by its vendor.

So the baseline states the discipline and stays vendor-neutral: read the
lockfile, read the installed source, and treat any documentation index as a
convenience for the last step that a session may or may not have.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "engineering-baseline" / "SKILL.md"

#: Identifiers of third-party documentation tools. Not a blocklist vise
#: maintains against the world — a cheap check that the section did not drift
#: into naming the one product whoever edits it happens to be using.
_VENDOR_NAMES = (
    "context7",
    "resolve-library-id",
    "get-library-docs",
    "query-docs",
    "ctx7",
    "devdocs",
    "readthedocs",
)


@pytest.fixture(scope="module")
def section() -> str:
    """The docs rule alone, so a vendor name elsewhere in the file is not
    silently credited to it."""
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("### Writing against a library")
    end = text.index("### ", start + 4)
    # Collapsed, because a rule that wraps across a line is the same rule and
    # an assertion that breaks on reflowed prose teaches people to delete it.
    return " ".join(text[start:end].split())


def test_the_baseline_carries_the_rule(section: str):
    """A rule that lives only in `researcher` reaches one agent of 22."""
    assert "installed source before recall" in section


def test_the_order_puts_this_disk_before_any_index(section: str):
    """Docs describe a release; the lockfile says which one runs here. Read in
    the other order and you get a correct answer about the wrong version."""
    lockfile = section.index("lockfile")
    installed = section.index("Read the installed source")
    outward = section.index("Only then go outward")
    assert lockfile < installed < outward


def test_the_rule_states_its_limits(section: str):
    """A rule with no stated limit gets applied where it does not hold, and
    then distrusted everywhere — the same reason the LSP rule states two."""
    assert "No source, no claim" in section
    assert "installed source wins" in section


def test_a_docs_tool_is_conditional_never_assumed(section: str):
    """Absent and unreadable are different. An unconditional 'look it up' is a
    step that silently no-ops in every session without that server, which is
    how a rule teaches confidence it did not earn."""
    assert "where one is configured" in section
    assert "if the session happens to have one" in section


@pytest.mark.parametrize("vendor", _VENDOR_NAMES)
def test_the_rule_names_no_vendor(section: str, vendor: str):
    """vise can check a name against a neighbour repository. It cannot check
    one against a hosted service, and `locate` is what that costs."""
    assert vendor not in section.casefold(), (
        f"engineering-baseline names {vendor!r}; nothing in this repository "
        "can tell when that name changes"
    )
