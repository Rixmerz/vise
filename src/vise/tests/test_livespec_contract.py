"""vise names livespec's tools in many places; it must name them consistently.

``read_unit``, ``locate``, ``search_similar`` — none of these are vise's. They
belong to livespec, a separate MCP, and vise writes them into the deny message
of ``codelayer_gate`` (its teaching surface: a deny that names a tool that does
not exist is a deny that gets routed around), two skills, three commands and
the README. Nothing in this repository can check those names against livespec.

What it can check is that every such name vise writes is one vise has decided
on, in ``vise.core.livespec.LIVESPEC_TOOLS``. A typo in a skill, a rename that
reached the hook but not the command, a pinned name nothing references any
more — each fails here, with the file.
"""
from __future__ import annotations

import re
from pathlib import Path

from vise.core.livespec import LIVESPEC_TOOLS

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
)

#: Backticked call-shaped identifiers: `read_unit(qname)`, `locate("x")`.
_CALL = re.compile(r"`([a-z][a-z0-9_]{2,})\(")

#: Things that look like tool calls in prose and are not tools of either server.
_NOT_TOOLS = frozenset({"open", "print", "len", "str", "int", "dict", "list"})


def _calls(path: Path) -> set[str]:
    return set(_CALL.findall(path.read_text(encoding="utf-8"))) - _NOT_TOOLS


def test_every_tool_call_an_asset_teaches_belongs_to_vise_or_livespec():
    known = LIVESPEC_TOOLS | VISE_TOOLS
    strays: dict[str, set[str]] = {}
    for path in SPEAKERS:
        unknown = _calls(path) - known
        if unknown:
            strays[str(path.relative_to(REPO))] = unknown
    assert not strays, (
        f"assets teach calls neither server exposes: {strays} — a rename on "
        f"livespec's side goes in vise.core.livespec, then here"
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
    """A name pinned that nothing says is a pin that outlived its reason."""
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in SPEAKERS)
    corpus += (REPO / "README.md").read_text(encoding="utf-8")
    dead = {name for name in LIVESPEC_TOOLS if name not in corpus}
    assert not dead, f"contracted but referenced nowhere: {sorted(dead)}"


def test_livespec_and_vise_never_share_a_name():
    """The same name on both servers would make the deny message ambiguous."""
    assert not (LIVESPEC_TOOLS & VISE_TOOLS)
