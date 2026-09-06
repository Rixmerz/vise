"""The workflow that moves code after the tests pass, not while writing it.

Its shape is unusual twice over. It is the only bundled graph whose looking is
done by *another server* — livespec's `search_similar` and `analyze_impact`,
which vise names in twenty-odd places and can call in none — and the only one
that ships deliberately unrouted, because its own proposal set the bar at three
real repositories before it goes in anybody's live path.

That split leaves a seam the design admits cannot be tested end to end: if the
agent fills a `Candidate` field wrongly, the refusal is wrong with it. What
*can* be tested is the half that lives here — that the prompt asking for those
fields and the dataclass receiving them name the same things, and that the
refusal rules the prompt teaches are the rules the code applies. These tests
are that half.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from vise.core.neighbours import LIVESPEC_TOOLS
from vise.engines.graph_parser import load_graph_from_file
from vise.runtime.decouple import Candidate, refuse

GRAPH = (Path(__file__).resolve().parents[1] / "assets" / "workflows"
         / "decouple-graph.yaml")


@pytest.fixture(scope="module")
def graph():
    return load_graph_from_file(GRAPH)


def _prompt(graph, node_id: str) -> str:
    return graph.nodes[node_id].prompt_injection or ""


def test_only_the_moving_phase_may_write(graph):
    """Survey, triage and report read. A phase that could edit while surveying
    would be deciding boundaries with the least information it will ever have,
    which is the thing this workflow exists to stop doing."""
    for node_id in ("survey", "triage", "report"):
        blocked = set(graph.nodes[node_id].tools_blocked)
        assert {"Edit", "Write"} <= blocked, f"{node_id} can write: {blocked}"
    assert not set(graph.nodes["move"].tools_blocked) & {"Edit", "Write"}


def test_the_survey_names_the_calls_it_cannot_make(graph):
    """The whole first phase is instructions to another server's tools. A
    prompt that named none would leave the agent to invent the lookup."""
    prompt = _prompt(graph, "survey")
    named = {tool for tool in LIVESPEC_TOOLS if tool in prompt}
    assert {"compute_index_status", "search_similar", "analyze_impact"} <= named, named


def test_the_prompt_asks_for_exactly_the_fields_the_code_receives(graph):
    """The seam the design says cannot be tested end to end — this is the half
    that can. An agent told to bring back `line_count` fills a `Candidate` that
    does not have one, and the refusal that follows is wrong for a reason
    nothing in either repository would report."""
    fields = {f.name for f in dataclasses.fields(Candidate)}
    prompt = _prompt(graph, "survey")
    missing = {name for name in fields if name not in prompt}
    assert not missing, f"the survey never names {missing}, so nothing will fill them"

    # And the other direction: a field the prompt invents is one the dataclass
    # rejects at construction, in the session, with nothing here to catch it.
    # Parsed from the block rather than matched against a list of the names
    # this test already knows — a filter naming them could only ever find them,
    # which is the shape of a test that pins nothing.
    body = prompt.split("and no others:", 1)[1]
    asked = set()
    for line in body.splitlines():
        if not line.strip():
            if asked:
                break
            continue
        asked.add(line.split()[0])
    assert asked == fields, f"the survey asks for {asked}, Candidate has {fields}"


def test_the_survey_says_a_field_it_cannot_fill_is_left_out(graph):
    """Every default refuses, so leaving one out is safe and guessing one is
    not — a guessed consumer count is the single number that can talk the rule
    of three into a move it should have declined."""
    prompt = _prompt(graph, "survey").lower()
    assert "cannot fill truthfully is left out" in prompt
    assert "do not estimate" in prompt


def test_the_triage_teaches_the_rules_by_the_names_the_code_returns(graph):
    """A prompt that described the rules in its own words would drift from the
    code the moment either changed, and the agent would be told twice."""
    prompt = _prompt(graph, "triage")
    rules = {
        refuse(Candidate(unit="u", path="tests/t.py", module_lines=400, consumers=9)),
        refuse(Candidate(unit="u", path="src/a.py", module_lines=400, consumers=1)),
        refuse(Candidate(unit="u", path="src/a.py", module_lines=10, consumers=9)),
    }
    assert rules == {"out_of_scope", "rule_of_three", "size_floor"}, rules
    for rule in rules:
        assert rule in prompt, f"the triage prompt never names {rule!r}"


def test_the_agent_is_told_it_may_disagree_and_may_not_act_on_it(graph):
    """The refusal list is code precisely so that the agent being asked to move
    code is not the one deciding whether it may. Saying only "these are the
    rules" invites the re-weighing the list exists to prevent."""
    prompt = _prompt(graph, "triage").lower()
    assert "you may disagree" in prompt
    assert "you may not act on it" in prompt


def test_a_red_test_is_reverted_rather_than_argued_with(graph):
    prompt = _prompt(graph, "move").lower()
    assert "reverted, not argued with" in prompt
    assert "one accepted candidate at a time" in prompt


def test_the_only_gate_is_the_suite_and_it_is_mechanical(graph):
    """This workflow moves code and changes no behaviour, so the suite that was
    green before is the whole of what it can be held to — and it is held to it
    by a validator, not by the agent saying the word."""
    move = graph.nodes["move"]
    assert [v["type"] for v in move.validators or []] == ["tests_pass"]
    out = [e for e in graph.edges if e.from_node == "move"]
    assert len(out) == 1 and out[0].condition.type == "validators_green", (
        "the exit from `move` is a phrase the agent can say"
    )


def test_the_scope_gate_ships_unconfigured_and_says_why(graph):
    """`diff_scope` needs `allow:` globs that belong to the consuming repo, and
    an empty `allow` fails closed. A shipped default would either block every
    run or permit everything, and the second reads as a gate while being none."""
    text = GRAPH.read_text(encoding="utf-8")
    assert "#   - type: diff_scope" in text, "the scope gate is not even offered"
    assert "FAILS CLOSED" in text and "worse than no gate" in text
    assert not (graph.nodes["move"].validators or [{}])[-1].get("allow"), (
        "diff_scope shipped with globs vise guessed for somebody else's repo"
    )


def test_no_index_leaves_the_phase_without_passing_through_move(graph):
    """The common case in any repo that has not mounted livespec. A phase that
    guessed at structure without an index is the under-engineering it exists to
    prevent, with a better name."""
    skip = [e for e in graph.edges if e.from_node == "survey" and e.to_node == "report"]
    assert skip, "no index means the survey has nowhere to go but forward"
    assert "no index" in skip[0].condition.phrases


def test_refusing_everything_is_an_outcome_with_its_own_exit(graph):
    """"Nothing to move" is what a calibrated refusal list produces most of the
    time. Routing it through a phase named `move` would make the good outcome
    take the path built for the other one."""
    out = [e for e in graph.edges if e.from_node == "triage" and e.to_node == "report"]
    assert out and "nothing to move" in out[0].condition.phrases


def test_the_report_keeps_skipped_apart_from_found_nothing(graph):
    """They render alike and mean opposite things: one is "I looked and there
    was nothing", the other is "I could not look"."""
    prompt = _prompt(graph, "report")
    # The distinguishing halves, not the labels. Checking that the words
    # "skipped" and "found: 0" appear passes on a prompt that lists them and
    # then explains neither — which is exactly the confusion being guarded.
    assert "the index could not be read" in prompt
    assert "there was nothing to move" in prompt
    assert "refusal is a finding" in prompt


def test_the_report_refuses_to_invent_a_confidence_number(graph):
    """The same rule the research and security workflows hold, for the same
    reason — and here there is an actual oracle, so a number would be worse."""
    assert "confidence number" in _prompt(graph, "report")


def test_the_workflow_is_unrouted_on_purpose_and_says_so(graph):
    """It ships to be runnable, not to be run: its own proposal set the bar at
    three real repositories first."""
    from .test_orchestration_skill_sync import _INTENTIONALLY_UNROUTED

    assert "decouple" in _INTENTIONALLY_UNROUTED
    assert "three real repos" in GRAPH.read_text(encoding="utf-8")
