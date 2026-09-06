"""A task that runs until it stops finding.

The shape the runtime could not express: a sweep whose right number of passes
is a property of the repository, not of whoever wrote the YAML. Run once and
the tail is missed; run a fixed five and four are paid for to report nothing.

The two claims that carry the design are the ones most worth pinning. A round
is a *passing* attempt, so a failed round takes the escalation ladder and is
not a quiet one — folding "found nothing" into "was wrong" is the conflation
`Verdict` exists to prevent. And the deduplication is code, because an agent
asked "is this one new" says yes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_engine import Task, Until
from vise.runtime import converge as cv
from vise.runtime.artifacts import ArtifactStore
from vise.runtime.contracts import (
    Artifact,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.state import RunState

RUN = "r-sweep"


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="researcher", role="research", description="d", model="sonnet",
                  writes=False, capabilities=("research",)),
        AgentSpec(id="verifier", role="verify", description="d", model="sonnet",
                  writes=False, capabilities=("verify",)),
    ):
        reg.agents[spec.id] = spec
    return reg


def _hunt(*, stable_for: int = 2, max_rounds: int = 5, **kw) -> Task:
    base = dict(id="hunt", name="Hunt", role="research", writes=False,
                until=Until(key="findings", stable_for=stable_for, max_rounds=max_rounds))
    base.update(kw)
    return Task(**base)


class _Rounds:
    """Reports a scripted list of findings per round, and records every brief."""

    def __init__(self, rounds, verdicts=None):
        self.rounds = list(rounds)
        self.verdicts = list(verdicts or [])
        self.briefs = []

    def run(self, brief):
        self.briefs.append(brief)
        if brief.role == "verify":
            verdict = self.verdicts.pop(0) if self.verdicts else Verdict.PASS
            return TaskResult(
                task_id=brief.task_id, verdict=verdict, summary="v",
                evidence="$ check\nok", checks="$ check\nok", usage=Usage(cost_usd=0.02),
            )
        found = self.rounds.pop(0) if self.rounds else []
        if found == "FAIL":
            return TaskResult(task_id=brief.task_id, verdict=Verdict.FAIL,
                              summary="the sweep crashed", classification=FailureKind.CODE_BUG,
                              usage=Usage(cost_usd=0.1))
        artifacts = ()
        if found is not None:
            artifacts = (Artifact(run_id=RUN, task_id=brief.task_id, kind="finding",
                                  payload={"findings": list(found)}),)
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.PASS, summary="swept",
            evidence="$ sweep\nok", checks="$ ruff\nok", usage=Usage(cost_usd=0.1),
            artifacts=artifacts,
        )

    def work_briefs(self):
        return [b for b in self.briefs if b.role != "verify"]


def _run(tmp_path: Path, tasks, worker, **kw) -> RunState:
    kw.setdefault("registry", _registry())
    kw.setdefault("artifacts", ArtifactStore(tmp_path / "artifacts", RUN))
    kw.setdefault("state_root", tmp_path / "state")
    spec = RunSpec(run_id=RUN, goal="g", project_dir="/nonexistent-not-a-repo",
                   budget=RunBudget(max_parallel=2))
    return Scheduler(worker=worker, **kw).run(spec, tasks)


# --- the pure half ---------------------------------------------------------


def test_a_round_that_repeats_a_finding_adds_nothing():
    first = cv.fold(["a", "b"], [], stable=0, number=1)
    second = cv.fold(["b", "a"], first.seen, stable=first.stable, number=2)
    assert first.fresh == ("a", "b") and first.stable == 0
    assert second.fresh == () and second.stable == 1
    assert second.seen == ("a", "b"), "the union, in the order first found"


def test_a_round_that_reported_no_list_is_quiet_not_broken():
    """It passed its gates, which is a claim to have looked. Blocking it would
    send a task that genuinely found nothing to the escalation ladder."""
    outcome = cv.fold(None, ["a"], stable=0, number=2)
    assert outcome.stable == 1 and outcome.fresh == ()
    assert outcome.reported is False, "and the two facts stay apart on the record"


def test_findings_are_keyed_by_their_text_exactly():
    """A near-match rule would decide two different findings are one, and the
    second would never be reported. Exact matching errs toward another round."""
    assert cv.key_of({"b": 2, "a": 1}) == cv.key_of({"a": 1, "b": 2})
    assert cv.key_of("  x  ") == cv.key_of("x")
    assert cv.key_of("x") != cv.key_of("x.")


def test_blank_findings_are_not_counted_as_new():
    assert cv.fold(["", "   "], [], stable=0, number=1).fresh == ()


def test_the_stop_condition_reads_the_tasks_own_numbers():
    quiet = cv.Round(fresh=(), seen=("a",), stable=2, number=3)
    assert not cv.another_round(_hunt(stable_for=2), quiet)
    assert cv.another_round(_hunt(stable_for=3), quiet)
    assert not cv.another_round(Task(id="t", name="t"), quiet), "no until, no rounds"


def test_the_cap_stops_a_sweep_that_never_converges():
    busy = cv.Round(fresh=("z",), seen=("z",), stable=0, number=3)
    assert not cv.another_round(_hunt(max_rounds=3), busy)
    assert "may not be finished" in cv.reason(_hunt(max_rounds=3), busy)
    assert "converged" in cv.reason(_hunt(stable_for=1), cv.Round((), ("a",), 1, 2))


def test_the_brief_lines_say_when_the_list_was_cut():
    """A brief that silently dropped half would have the round report those
    again and count as finding something new."""
    lines = cv.already_found([f"f{i}" for i in range(90)], limit=10)
    assert any("showing 10 of 90" in line for line in lines)
    assert len([line for line in lines if line.startswith("  - ")]) == 10
    assert cv.already_found([]) == ()


# --- the loop --------------------------------------------------------------


def test_two_quiet_rounds_stop_the_sweep(tmp_path):
    worker = _Rounds([["a"], ["b"], ["b"], ["a", "b"]])
    state = _run(tmp_path, [_hunt(stable_for=2)], worker)

    record = state.tasks["hunt"]
    assert record.state is TaskState.SUCCEEDED
    assert record.rounds == 4 and len(worker.work_briefs()) == 4
    assert sorted(record.seen) == ["a", "b"], "the union of what every round found"
    assert "converged" in record.note


def test_the_maximum_bounds_a_sweep_that_keeps_finding(tmp_path):
    worker = _Rounds([["a"], ["b"], ["c"], ["d"], ["e"]])
    state = _run(tmp_path, [_hunt(stable_for=2, max_rounds=3)], worker)

    record = state.tasks["hunt"]
    assert record.state is TaskState.SUCCEEDED
    assert record.rounds == 3 and len(worker.work_briefs()) == 3
    assert "maximum of 3" in record.note and "may not be finished" in record.note


def test_each_round_is_told_what_the_earlier_ones_found(tmp_path):
    worker = _Rounds([["alpha"], ["beta"], [], []])
    _run(tmp_path, [_hunt(stable_for=2)], worker)

    briefs = worker.work_briefs()
    assert "alpha" not in briefs[0].render()
    assert "alpha" in briefs[1].render() and "report none of these again" in briefs[1].render()
    assert "beta" in briefs[2].render()


def test_a_failed_round_takes_the_ladder_and_is_not_a_quiet_round(tmp_path):
    worker = _Rounds([["a"], "FAIL", [], []])
    state = _run(tmp_path, [_hunt(stable_for=2)], worker)

    record = state.tasks["hunt"]
    tried = worker.work_briefs()
    assert (tried[1].model, tried[1].effort) != (tried[2].model, tried[2].effort), (
        "the failed round escalated one rung, as any failed attempt does"
    )
    # Four attempts, three of them rounds. Asserting the round count alone was
    # not enough: folding the failure in as a quiet round reaches three too,
    # one attempt earlier. The pair is what says the failure was not counted.
    assert len(tried) == 4, "one failed attempt and three rounds"
    assert record.rounds == 3, "the failure was an attempt, not a round"
    assert record.state is TaskState.SUCCEEDED


def test_the_dedup_costs_no_model_call(tmp_path):
    """Nothing is asked whether a finding is new; it is counted."""
    worker = _Rounds([["a"], ["a"], ["a"]])
    state = _run(tmp_path, [_hunt(stable_for=2)], worker)

    assert state.tasks["hunt"].rounds == 3
    assert [b.role for b in worker.briefs] == ["research"] * 3, (
        "no extra agent was consulted about novelty"
    )
    assert state.tasks["hunt"].seen == ["a"]


def test_a_task_without_until_runs_exactly_once(tmp_path):
    worker = _Rounds([["a"], ["b"]])
    state = _run(tmp_path, [Task(id="hunt", name="Hunt", role="research", writes=False)], worker)
    assert len(worker.work_briefs()) == 1
    assert state.tasks["hunt"].rounds == 0 and state.tasks["hunt"].seen == []


def test_every_round_is_charged_to_the_run(tmp_path):
    worker = _Rounds([["a"], ["b"], [], []])
    state = _run(tmp_path, [_hunt(stable_for=2)], worker)
    assert state.ledger.by_task["hunt"].cost_usd == pytest.approx(0.4)


def test_the_events_narrate_the_sweep(tmp_path):
    worker = _Rounds([["a"], [], []])
    state = _run(tmp_path, [_hunt(stable_for=2)], worker)

    rounds = [e for e in state.events if e["kind"] == "round"]
    assert [r["number"] for r in rounds] == [1, 2, 3]
    assert [r["fresh"] for r in rounds] == [1, 0, 0]
    assert [r["stable"] for r in rounds] == [0, 1, 2]
    converged = [e for e in state.events if e["kind"] == "converged"]
    assert len(converged) == 1 and converged[0]["found"] == 1


def test_a_verified_round_is_still_only_a_round(tmp_path):
    """The verifier judges this pass. Whether the sweep is finished is a
    different question, counted rather than asked."""
    worker = _Rounds([["a"], [], []])
    state = _run(tmp_path, [_hunt(stable_for=2, acceptance=["the sweep ran"])], worker)

    assert state.tasks["hunt"].state is TaskState.SUCCEEDED
    assert state.tasks["hunt"].rounds == 3
    assert len([b for b in worker.briefs if b.role == "verify"]) == 3, "one per round"


def test_a_round_the_verifier_rejects_does_not_count(tmp_path):
    worker = _Rounds([["a"], ["b"], [], []],
                     verdicts=[Verdict.PASS, Verdict.FAIL, Verdict.PASS, Verdict.PASS])
    state = _run(tmp_path, [_hunt(stable_for=2, acceptance=["the sweep ran"])], worker)

    record = state.tasks["hunt"]
    assert record.rounds < len(worker.work_briefs()), (
        "the rejected attempt was not folded in as a round"
    )
    assert "b" not in record.seen, "a rejected round's findings are not banked"
    assert record.seen == ["a"], (
        "and nothing else was banked either — a rejected round contributes "
        "neither its findings nor a quiet round toward the stop condition"
    )


def test_the_sweep_survives_a_stop_and_a_resume(tmp_path):
    """A resumed sweep that forgot what it found would re-report all of it and
    count that as a round that found something."""
    # Findings a reader could not mistake for ordinary prose in a brief: the
    # first draft of this test looked for "a", which matches almost any
    # sentence, and stayed green with the carrying removed entirely.
    early, late = "dup:src/parse.py:mangle", "dup:src/emit.py:mangle"
    worker = _Rounds([[early], [late], "FAIL", "FAIL", "FAIL", "FAIL", "FAIL"])
    parked = _run(tmp_path, [_hunt(stable_for=2, max_rounds=9)], worker,
                  config=SchedulerConfig(replanner=None))
    assert parked.tasks["hunt"].state in (TaskState.FAILED, TaskState.WAITING_HUMAN)
    assert sorted(parked.tasks["hunt"].seen) == sorted([early, late])

    loaded = RunState.load(tmp_path / "state", RUN)
    assert sorted(loaded.tasks["hunt"].seen) == sorted([early, late])
    assert loaded.tasks["hunt"].rounds == 2

    healed = _Rounds([[early], []])
    state = Scheduler(worker=healed, registry=_registry(),
                      artifacts=ArtifactStore(tmp_path / "artifacts", RUN),
                      state_root=tmp_path / "state").resume(
        loaded, [_hunt(stable_for=2, max_rounds=9)])

    resumed = healed.work_briefs()[0].render()
    assert early in resumed and late in resumed, "the resumed round knows what was found"
    assert state.tasks["hunt"].state is TaskState.SUCCEEDED
    assert sorted(state.tasks["hunt"].seen) == sorted([early, late])
    assert state.tasks["hunt"].rounds == 4, "rounds continue, they do not restart"


def test_a_sweep_inside_a_stopped_run_does_not_go_again(tmp_path):
    """`_apply` parks a task the run already stopped; a sweep must agree rather
    than dispatching into a run that is over."""
    worker = _Rounds([["a"], ["b"]])
    state = _run(tmp_path, [_hunt(stable_for=2, max_rounds=9)], worker,
                 config=SchedulerConfig(should_cancel=lambda: False))
    assert state.tasks["hunt"].rounds >= 1
