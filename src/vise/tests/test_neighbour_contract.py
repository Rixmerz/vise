"""vise names its neighbours' tools in many places; it must name them consistently.

``read_unit``, ``search_similar``, ``detect_issues`` — none of these are vise's.
They belong to livespec and layout-inspector, separate MCP servers that vise
runs beside and cannot call, and vise writes them into the deny message of
``codelayer_gate`` (its teaching surface: a deny that names a tool that does not
exist is a deny that gets routed around), two skills, three commands, a workflow
and the README. Nothing in this repository can check those names against either
server.

What it can check is that every such name vise writes is one vise has decided
on, in ``vise.core.neighbours``. A typo in a skill, a rename that reached the
hook but not the command, a pinned name nothing references any more — each
fails here, with the file.
"""
from __future__ import annotations

import re
from pathlib import Path

from vise.core.neighbours import (
    LAYOUT_INSPECTOR_TOOLS,
    LIVESPEC_TOOLS,
    NEIGHBOUR_TOOLS,
)

from .test_asset_honesty import VISE_TOOLS

REPO = Path(__file__).resolve().parents[3]

#: Every asset that talks about the symbol layer. Adding one here is the
#: whole cost of putting it under the contract.
SPEAKERS = (
    REPO / "skills" / "codelayer" / "SKILL.md",
    REPO / "skills" / "orchestration" / "SKILL.md",
    REPO / "commands" / "codelayer.md",
    REPO / "commands" / "debt.md",
    REPO / "commands" / "bootstrap.md",
    REPO / "src" / "vise" / "hooks" / "codelayer_gate.py",
    # The decouple workflow is the only *graph* that speaks livespec: its
    # survey phase is a list of calls vise cannot make, written down for the
    # agent that can. A rename there is as silent as one in a skill.
    REPO / "src" / "vise" / "assets" / "workflows" / "decouple-graph.yaml",
    # The debug workflow's tracing node is the only graph that speaks
    # flowtrace, and the verification panel's regression lens is the only
    # Python that names a neighbour's call in a prompt an agent will run.
    # Both are as silent about a rename as any skill.
    REPO / "src" / "vise" / "assets" / "workflows" / "debug-graph.yaml",
    REPO / "src" / "vise" / "runtime" / "verify.py",
)

#: Backticked call-shaped identifiers: `read_unit(qname)`, `locate("x")`.
_CALL = re.compile(r"`([a-z][a-z0-9_]{2,})\(")

#: Things that look like tool calls in prose and are not tools of either server.
_NOT_TOOLS = frozenset({"open", "print", "len", "str", "int", "dict", "list"})


def _calls(path: Path) -> set[str]:
    return set(_CALL.findall(path.read_text(encoding="utf-8"))) - _NOT_TOOLS


def test_every_tool_call_an_asset_teaches_belongs_to_vise_or_a_neighbour():
    known = NEIGHBOUR_TOOLS | VISE_TOOLS
    strays: dict[str, set[str]] = {}
    for path in SPEAKERS:
        unknown = _calls(path) - known
        if unknown:
            strays[str(path.relative_to(REPO))] = unknown
    assert not strays, (
        f"assets teach calls no server exposes: {strays} — a rename on a "
        f"neighbour's side goes in vise.core.neighbours, then here"
    )


def test_the_deny_message_names_only_contracted_tools():
    """The hook is the surface a routed-around agent sees first."""
    hook = (REPO / "src" / "vise" / "hooks" / "codelayer_gate.py").read_text(encoding="utf-8")
    named = set(re.findall(r"\b([a-z_]+)\(", hook)) & (LIVESPEC_TOOLS | {
        n for n in re.findall(r"\b([a-z_]+)\(", hook) if n.endswith(("_unit", "_similar", "_location"))
    })
    assert named, "the deny message no longer names any symbol tool — the teaching surface is gone"
    assert named <= LIVESPEC_TOOLS, named - LIVESPEC_TOOLS


def test_no_contracted_name_is_dead():
    """A name pinned that nothing says is a pin that outlived its reason.

    This is why a neighbour's *full* tool surface does not belong in the
    contract — only the part vise actually teaches.
    """
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in SPEAKERS)
    corpus += (REPO / "README.md").read_text(encoding="utf-8")
    dead = {name for name in NEIGHBOUR_TOOLS if name not in corpus}
    assert not dead, f"contracted but referenced nowhere: {sorted(dead)}"


def test_no_neighbour_shares_a_name_with_vise():
    """The same name on two servers would make the deny message ambiguous."""
    assert not (NEIGHBOUR_TOOLS & VISE_TOOLS)


def test_the_neighbours_do_not_share_a_name_with_each_other():
    """A brief that told a builder to call one would be naming either."""
    assert not (LIVESPEC_TOOLS & LAYOUT_INSPECTOR_TOOLS)


def test_the_union_is_the_whole_of_both():
    """Guard against a third neighbour landing in the module and never
    reaching the union every other test in this file checks against."""
    import vise.core.neighbours as neighbours

    sets = {
        name: value for name, value in vars(neighbours).items()
        if name.endswith("_TOOLS") and name != "NEIGHBOUR_TOOLS"
    }
    assert len(sets) >= 2
    missing = {n: v - NEIGHBOUR_TOOLS for n, v in sets.items() if v - NEIGHBOUR_TOOLS}
    assert not missing, f"contracted but outside NEIGHBOUR_TOOLS: {missing}"
