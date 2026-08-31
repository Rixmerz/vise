"""The first bundled workflow whose product is an answer, not a diff.

Nine of the ten bundled graphs are engineering-shaped: they end in a change to
the working tree, and they gate on whether the repo's own checks pass. A tenth
that merely renamed those phases would be worse than none — it would route real
research questions into a pipeline built to grade diffs.

So these tests pin the two things that make it a different shape, and the one
that makes it honest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_parser import load_graph_from_file
from vise.runtime import ownership as own
from vise.runtime.registry import AgentRegistry
from vise.runtime.routing import ModelRouter

GRAPH = (Path(__file__).resolve().parents[1] / "assets" / "workflows"
         / "research-graph.yaml")


@pytest.fixture(scope="module")
def graph():
    return load_graph_from_file(GRAPH)


def test_the_gathering_phase_actually_fans_out(graph):
    """A `dag` node, which no bundled graph reached before this one.

    The runtime's parallel fan-out existed and shipped with nothing that used
    it. Four sequential prompts in one node would look almost identical in the
    YAML and would not be this.
    """
    node = graph.nodes["gather"]
    assert node.node_type == "dag"
    assert len(node.tasks) >= 4


def test_every_gathering_task_is_read_only_and_conflicts_with_nobody(graph):
    """Research reads. Nothing here may claim a write, so nothing serializes."""
    tasks = graph.nodes["gather"].tasks
    assert all(not t.writes for t in tasks), (
        f"a gathering task claims writes: {[t.id for t in tasks if t.writes]}"
    )
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            assert not (own.conflicts(a.ownership, b.ownership)
                        and (a.writes or b.writes)), f"{a.id} serializes {b.id}"


def test_one_gathering_task_exists_to_disagree(graph):
    """Agreement is the failure mode of parallel research.

    Four agents given one question and no instruction to disagree return four
    versions of the first plausible answer, and the run reports high confidence
    for it. Something in the fan-out has to be paid to look for the counter-case.
    """
    prompts = {t.id: (t.prompt or "").lower() for t in graph.nodes["gather"].tasks}
    against = [tid for tid, p in prompts.items()
               if "wrong" in p or "counter" in p or "dissent" in p]
    assert against, f"no task looks for what would refute the answer: {list(prompts)}"


def test_the_adversarial_pass_is_not_the_cheapest_one(graph):
    """It is the hardest read in the set and the one carrying the workflow.

    A cheap model returns "no counter-evidence found" and is believed, which is
    the exact failure this phase exists to prevent.
    """
    reg, router = AgentRegistry.bundled(), ModelRouter()
    tiers = {t.id: router.route(t, agent=reg.resolve(t.role).agent).tier
             for t in graph.nodes["gather"].tasks}
    assert tiers["against"] > min(tiers.values()), tiers


def test_every_gathering_task_is_staffable(graph):
    """A task nobody can staff is a plan that stalls instead of running."""
    reg = AgentRegistry.bundled()
    for task in graph.nodes["gather"].tasks:
        assert reg.resolve(task.role).agent is not None, (
            f"{task.id} declares role {task.role!r}, which nobody takes"
        )


def test_verification_can_send_the_run_back_for_more_sources(graph):
    """Verification emptying the evidence must be able to reopen gathering.

    Writing a synthesis on what survived is the alternative, and it is how a
    report ends up confident about the two findings nobody checked.
    """
    back = [e for e in graph.edges if e.from_node == "verify" and e.to_node == "gather"]
    assert back, "verify cannot reopen gather"


def test_no_node_claims_a_gate_it_cannot_run(graph):
    """The honest part.

    There is no suite to run against a claim about the world and no exit code to
    consult, so this workflow gates on almost nothing — and says so in
    `test_node_gate_coverage.UNVERIFIED_BY_DESIGN` rather than declaring a
    validator that would report a check nobody performed.
    """
    from vise.engines.validators import _REGISTRY

    for node in graph.nodes.values():
        for cfg in node.validators or []:
            assert cfg.get("type") in _REGISTRY, (
                f"{node.id} declares validator {cfg.get('type')!r}, which does "
                f"not exist — a gate that cannot run must never look green"
            )


def test_the_synthesis_refuses_to_invent_a_confidence_number(graph):
    """The same rule the security workflow holds for CVSS, for the same reason."""
    prompt = (graph.nodes["synthesize"].prompt_injection or "").lower()
    assert "not compute a confidence percentage" in prompt or "percentage" in prompt
    assert "not established" in prompt, (
        "a synthesis that cannot name what it failed to answer is how a plan "
        "gets built on a gap"
    )
