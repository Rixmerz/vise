"""A retry waits, and parallel retries do not wait in lockstep.

Before this existed the second attempt landed 2.5 ms after the first, which is
shorter than anything it could be waiting out, and five parallel retries fired
inside a 1.1 ms window — the herd that produces the rate limit being retried.

The delay is asserted here and nowhere else: every other scheduler test drives
`backoff_base_s` to zero, because what a retry *does* is a different question
from how long it waits first.
"""
from __future__ import annotations

import time

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import (
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.recovery import (
    RETRY_BASE_S,
    RETRY_CAP_S,
    decide,
    retry_delay_s,
)
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.state import RunState, TaskRecord


# --- the policy ----------------------------------------------------------


@pytest.mark.parametrize("retries_taken", range(5))
def test_the_delay_is_never_below_half_the_exponential(retries_taken):
    """The floor is the property that matters: an environment failure gets one
    retry, and a draw near zero would spend it inside the window it waits out."""
    ceiling = min(RETRY_CAP_S, RETRY_BASE_S * 2**retries_taken)

    lowest = retry_delay_s(retries_taken, rand=lambda: 0.0)
    highest = retry_delay_s(retries_taken, rand=lambda: 1.0)

    assert lowest == pytest.approx(ceiling / 2)
    assert highest == pytest.approx(ceiling)


def test_the_delay_grows_with_the_retries_already_taken():
    at_lowest = [retry_delay_s(n, rand=lambda: 0.0) for n in range(4)]

    assert at_lowest == sorted(at_lowest)
    assert at_lowest[1] == pytest.approx(at_lowest[0] * 2)


def test_the_delay_is_capped_however_many_retries_have_been_taken():
    assert retry_delay_s(50, rand=lambda: 1.0) == pytest.approx(RETRY_CAP_S)


def test_a_draw_outside_zero_to_one_cannot_push_past_the_cap():
    """A stub that returns 3.0 is a bug in the stub, not a licence to wait
    three times the ceiling."""
    assert retry_delay_s(0, rand=lambda: 3.0) == pytest.approx(RETRY_BASE_S)
    assert retry_delay_s(0, rand=lambda: -1.0) == pytest.approx(RETRY_BASE_S / 2)


def test_a_fixed_random_source_reproduces_the_delay():
    first = retry_delay_s(2, rand=lambda: 0.375)
    second = retry_delay_s(2, rand=lambda: 0.375)

    assert first == second


def test_no_base_means_no_wait_rather_than_a_small_one():
    """What a test drives to keep the suite fast has to mean nothing happens."""
    assert retry_delay_s(0, base_s=0.0) == 0.0
    assert retry_delay_s(3, base_s=0.0) == 0.0
    assert retry_delay_s(0, base_s=-1.0) == 0.0


def test_the_draw_actually_spreads_the_retries():
    """Backoff alone leaves identical delays identical. This is the half that
    decorrelates, so a hundred draws must not collapse onto one value."""
    draws = {retry_delay_s(0) for _ in range(100)}

    assert len(draws) > 50
    assert all(RETRY_BASE_S / 2 <= d <= RETRY_BASE_S for d in draws)


def test_the_decision_itself_reads_no_clock_and_draws_nothing():
    """`decide` stays reproducible from the record — only *when* is random."""
    result = TaskResult(task_id="a", verdict=Verdict.FAIL, summary="wrong",
                        classification=FailureKind.CODE_BUG)

    moves = {decide(result).to_dict()["action"] for _ in range(20)}

    assert len(moves) == 1


# --- the deadline survives a resume --------------------------------------


def test_the_deadline_survives_a_state_round_trip():
    """Epoch seconds, not monotonic: a monotonic deadline read back after a
    restart is a number about a clock that no longer exists."""
    record = TaskRecord(task_id="a", not_before=1789012345.5)

    back = TaskRecord.from_dict(record.to_dict())

    assert back.not_before == 1789012345.5


def test_a_state_file_written_before_this_existed_owes_no_wait():
    assert TaskRecord.from_dict({"task_id": "a"}).not_before == 0.0


# --- the scheduler -------------------------------------------------------


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.agents["reviewer"] = AgentSpec(
        id="reviewer", role="review", description="d", model="sonnet",
        writes=False, capabilities=("review",))
    # `review` resolves straight to the top rung, so a task with that role can
    # never escalate. The escalation test needs a role with room to climb.
    reg.agents["backend-python"] = AgentSpec(
        id="backend-python", role="backend", description="d", model="sonnet",
        writes=False, capabilities=("backend", "python"))
    return reg


def _spec(**kw) -> RunSpec:
    base = dict(run_id="r1", goal="g", project_dir="/nonexistent-not-a-repo",
                budget=RunBudget(max_parallel=4))
    base.update(kw)
    return RunSpec(**base)


def _task(task_id: str) -> Task:
    return Task(id=task_id, name=task_id, role="review", writes=False)


def _pass(task_id: str) -> TaskResult:
    return TaskResult(task_id=task_id, verdict=Verdict.PASS, summary="ok",
                      evidence="$ pytest\nok", checks="$ ruff\nok",
                      usage=Usage(cost_usd=0.0))


def _env_fail(task_id: str) -> TaskResult:
    return TaskResult(task_id=task_id, verdict=Verdict.INCONCLUSIVE,
                      summary="the session errored: rate_limit_error",
                      classification=FailureKind.ENVIRONMENT_BUG,
                      usage=Usage(cost_usd=0.0))


class ScriptedWorker:
    """Fails the named tasks on their first attempt, passes everything after."""

    def __init__(self, fails_once: set[str]) -> None:
        self.fails_once = set(fails_once)
        self.calls: list[str] = []

    def run(self, brief):
        self.calls.append(brief.task_id)
        if brief.task_id in self.fails_once:
            self.fails_once.discard(brief.task_id)
            return _env_fail(brief.task_id)
        return _pass(brief.task_id)


def _run(tasks, worker, *, base_s: float, **kw) -> RunState:
    spec_kw = kw.pop("spec_kw", {})
    config = SchedulerConfig(verify=False, backoff_base_s=base_s)
    return Scheduler(worker=worker, registry=_registry(), config=config,
                     **kw).run(_spec(**spec_kw), tasks)


def test_an_environment_failure_is_held_back_and_the_run_says_for_how_long():
    worker = ScriptedWorker({"a"})

    state = _run([_task("a")], worker, base_s=0.2)

    armed = [e for e in state.events if e["kind"] == "backoff"]
    assert len(armed) == 1, "the wait is announced once, when it is armed"
    assert armed[0]["task"] == "a"
    assert 0.1 <= armed[0]["delay_s"] <= 0.2
    assert state.record("a").state is TaskState.SUCCEEDED


def test_an_escalation_does_not_wait():
    """A bigger model is not waiting for a condition to clear, so a delay there
    would buy wall clock and nothing else.

    The rung assertion is what keeps this honest. Written against a `review`
    task it passed while arming the backoff on escalation too, because `review`
    resolves to the top rung and the run never escalated at all.
    """
    class AlwaysWrong:
        def __init__(self) -> None:
            self.rungs: list[str] = []

        def run(self, brief):
            self.rungs.append(f"{brief.model}/{brief.effort}")
            return TaskResult(task_id=brief.task_id, verdict=Verdict.FAIL,
                              summary="wrong", classification=FailureKind.CODE_BUG,
                              usage=Usage(cost_usd=0.0))

    worker = AlwaysWrong()
    task = Task(id="a", name="a", role="backend", writes=False)

    state = _run([task], worker, base_s=5.0)

    assert len(set(worker.rungs)) > 1, f"nothing escalated: {worker.rungs}"
    assert [e for e in state.events if e["kind"] == "backoff"] == []
    assert state.record("a").not_before == 0.0


def test_a_waiting_retry_does_not_hold_up_a_task_that_is_ready():
    """The whole reason the wait is recorded rather than slept on. With one
    dispatch slot, a blocking implementation would run 'a' twice before 'b'
    ever started."""
    worker = ScriptedWorker({"a"})

    _run([_task("a"), _task("b")], worker, base_s=0.4,
         spec_kw={"budget": RunBudget(max_parallel=1)})

    assert worker.calls == ["a", "b", "a"], worker.calls


def test_a_run_whose_only_work_is_a_waiting_retry_does_not_finish_early():
    """The loop reports a run stalled when nothing dispatched and nothing is in
    flight. A task waiting out its backoff is neither, and without the check it
    would be marked blocked and the retry would never happen."""
    worker = ScriptedWorker({"a"})

    state = _run([_task("a")], worker, base_s=0.3)

    assert state.record("a").state is TaskState.SUCCEEDED
    assert worker.calls == ["a", "a"]
    assert state.succeeded()


def test_the_wait_is_real_time_and_not_a_bookkeeping_entry():
    worker = ScriptedWorker({"a"})

    started = time.monotonic()
    _run([_task("a")], worker, base_s=0.6)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.3, "at least half the base, which is the jitter floor"


def test_parallel_failures_do_not_retry_in_the_same_instant():
    """Five retries inside 1.1 ms was the measurement that motivated jitter."""
    class Timed(ScriptedWorker):
        def __init__(self, fails_once):
            super().__init__(fails_once)
            self.at: list[tuple[str, float]] = []

        def run(self, brief):
            self.at.append((brief.task_id, time.monotonic()))
            return super().run(brief)

    ids = [f"t{i}" for i in range(5)]
    worker = Timed(set(ids))

    _run([_task(i) for i in ids], worker, base_s=0.8,
         spec_kw={"budget": RunBudget(max_parallel=5)})

    seconds = sorted(at for task_id, at in worker.at[5:])
    assert len(seconds) == 5
    assert seconds[-1] - seconds[0] > 0.02, "retries landed in lockstep"
