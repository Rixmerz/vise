"""Two design agents, two roles, and the tie that would have broken replan.

The fleet carried one `designer`, which decided what a UI *looked like* —
palette, type scale, layout, one signature element. Nothing owned what it
*did*: which screens exist, what happens on each, and the states each one can
be in. So a feature arrived styled and built to its happy path, and everything
a person meets on the second day — the list before it has rows, the request
that takes four seconds, the record whose title runs to two hundred characters
— was left to whoever was implementing, at the end, under time pressure. That
is the distance between a UI that works and one that shipped, and it is
enumerable rather than a matter of taste.

`ux-designer` owns it, ahead of `ui-designer`, and `ui-critique` carries the
enumeration both of them and `frontend` work from.

The trap this file mostly exists for is the role map. `replan.REMEDIATION_ROLE`
is `"design"`, and `Registry.resolve` refuses to break a tie alphabetically —
by design, because twelve agents take `backend` and picking the first one sends
a Python task to the C++ charter. Giving both design agents the `design` role
would therefore have left **every remediation task unroutable**, silently, to
buy a name nobody dispatches by. `ux-designer` takes `ux` instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.runtime.registry import AgentRegistry
from vise.runtime.replan import REMEDIATION_ROLE

REPO = Path(__file__).resolve().parents[3]
AGENTS = REPO / "agents"
WORKFLOW = REPO / "src" / "vise" / "assets" / "workflows" / "ui-feature-graph.yaml"


def _collapsed(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.fixture(scope="module")
def registry() -> AgentRegistry:
    return AgentRegistry.from_dir(AGENTS)


# --- the role map ----------------------------------------------------------

def test_the_remediation_role_still_resolves_to_exactly_one_agent(registry):
    """The regression a shared role would have caused, asserted at its source.

    `resolve` returning None here is not a crash — the run reports the task
    unroutable and carries on, which is why nothing else would have caught it.
    """
    resolution = registry.resolve(REMEDIATION_ROLE)
    assert resolution.agent is not None, (
        f"no single agent takes role {REMEDIATION_ROLE!r} — replan's remediation "
        f"task is unroutable. Reason given: {resolution.reason}"
    )
    assert resolution.agent.id == "ui-designer"


def test_the_two_design_agents_take_different_roles(registry):
    ux = registry.resolve("ux").agent
    assert ux is not None and ux.id == "ux-designer"
    assert registry.resolve("design").agent.id != ux.id


# --- the split itself ------------------------------------------------------

def test_the_flow_agent_does_not_claim_the_visual_decision():
    """Both charters have to disclaim the other's half, or the first one
    dispatched does both and the second is a second opinion."""
    charter = _collapsed(AGENTS / "ux-designer.md")
    assert "DON'T decide palette, type scale, or the signature element." in charter
    assert "`ui-designer`'s brief and it comes after yours" in charter


def test_the_visual_agent_does_not_claim_the_flow():
    charter = _collapsed(AGENTS / "ui-designer.md")
    assert "DON'T decide which screens exist" in charter
    assert "That is `ux-designer`'s brief, it comes first" in charter


@pytest.mark.parametrize("name", ["ux-designer", "ui-designer", "frontend"])
def test_everyone_who_owns_a_state_preloads_the_enumeration(name, registry):
    """`ui-critique` is where the state set lives. An agent that has to build,
    style or decide a state and does not preload it is working from memory of
    what a finished screen contains, which is the original failure."""
    agent = registry.agents[name]
    assert "ui-critique" in agent.skills, f"{name} does not preload ui-critique"


# --- the workflow ----------------------------------------------------------

def test_states_is_its_own_phase_between_build_and_verify():
    """Folded into `build`, the states are the work that gets cut the moment
    the happy path is demoable. Its own node means its own exit signal, so a
    run that skipped it is visible in the timeline and not only in the
    product."""
    import yaml

    graph = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    ids = [n["id"] for n in graph["nodes"]]
    assert ids.index("build") < ids.index("states") < ids.index("verify")

    froms = {(e["from"], e["to"]) for e in graph["edges"]}
    assert ("build", "states") in froms
    assert ("states", "verify") in froms


def test_the_deciding_phases_come_before_any_code_and_block_edits():
    import yaml

    graph = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    ids = [n["id"] for n in graph["nodes"]]

    assert ids.index("flow") < ids.index("look") < ids.index("build"), (
        "the visual direction must be decided after the flow, not before — a "
        "palette applied to a dead end is the more expensive mistake because "
        "it looks finished"
    )
    for phase in ("flow", "look"):
        assert "Edit" in (nodes[phase].get("tools_blocked") or []), (
            f"{phase} no longer blocks Edit; a deciding phase that can edit "
            f"source stops being one"
        )


def test_the_verify_node_carries_the_gate_that_can_never_skip():
    """`design_tokens` reads source and needs nothing installed, so unlike the
    two render gates it can never skip for being unavailable. On a UI workflow
    it is the one gate that always has something to say."""
    import yaml

    graph = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    verify = next(n for n in graph["nodes"] if n["id"] == "verify")
    types = {v["type"] for v in (verify.get("validators") or [])}
    assert "design_tokens" in types
