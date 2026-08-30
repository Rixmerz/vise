"""Stress, real concurrency, and failure injection.

The unit tests say each rule is implemented. These say the rules still hold when
fifty tasks run against four threads and a third of them fail — which is where a
scheduler's real bugs live: a task lost between states, two writers overlapping
for a few milliseconds, a loop that never terminates because every exit is
guarded by a condition that a retry resets.

Everything here is deterministic. A stress test that fails one run in twenty
teaches people to re-run it.
"""
from __future__ import annotations

import random
import threading
import time

import pytest

from vise.engines.graph_engine import Task
from vise.runtime import ownership as _own
from vise.runtime.contracts import (
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
from vise.runtime.worker import MockWorker

TERMINAL_OR_PARKED = {
    TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED,
    TaskState.BLOCKED, TaskState.WAITING_HUMAN,
}


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.agents["worker"] = AgentSpec(
        id="worker", role="backend", description="d", model="sonnet",
        capabilities=("backend",),
    )
    reg.agents["looker"] = AgentSpec(
        id="looker", role="review", description="d", model="sonnet",
        writes=False, capabilities=("review",),
    )
    return reg


def _spec(**kw) -> RunSpec:
    base = dict(run_id="stress", goal="stress", project_dir="/nonexistent-not-a-repo",
                budget=RunBudget(max_parallel=4))
    base.update(kw)
    return RunSpec(**base)


def _graph(n: int, seed: int, *, areas: int = 6) -> list[Task]:
    """A deterministic pseudo-random DAG: task i may depend only on j < i."""
    rng = random.Random(seed)
    tasks: list[Task] = []
    for i in range(n):
        deps = [f"t{j}" for j in sorted(rng.sample(range(i), min(i, rng.randint(0, 2))))]
        area = rng.randrange(areas)
        tasks.append(Task(
            id=f"t{i}", name=f"t{i}", role="backend",
            ownership=[f"src/area{area}/**"],
            dependencies=deps,
        ))
    return tasks


def _run(tasks, worker, **kw):
    kw.setdefault("registry", _registry())
    spec = kw.pop("spec", _spec())
    return Scheduler(worker=worker, **kw).run(spec, tasks)


# --- every task ends somewhere -------------------------------------------


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_no_task_is_left_in_flight_however_the_graph_is_shaped(seed):
    state = _run(_graph(40, seed), MockWorker())
    assert state.is_done()
    for record in state.tasks.values():
        assert record.state in TERMINAL_OR_PARKED, f"{record.task_id} ended {record.state}"


@pytest.mark.parametrize("seed", [3, 11])
def test_a_clean_run_of_forty_tasks_succeeds_entirely(seed):
    state = _run(_graph(40, seed), MockWorker())
    assert state.succeeded()
    assert len(state.tasks) == 40


def test_a_deep_chain_runs_in_order():
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="backend", ownership=[f"src/{i}/**"],
             dependencies=[f"t{i - 1}"] if i else [])
        for i in range(30)
    ]
    worker = MockWorker()
    state = _run(tasks, worker)
    assert state.succeeded()
    assert [b.task_id for b in worker.briefs] == [f"t{i}" for i in range(30)]


# --- real concurrency -----------------------------------------------------


class _OverlapDetector(MockWorker):
    """Records genuine temporal overlap between tasks, not just dispatch order."""

    def __init__(self, hold_s: float = 0.02, **kw):
        super().__init__(**kw)
        self.hold_s = hold_s
        self._lock = threading.Lock()
        self._live: set[str] = set()
        self.overlaps: list[tuple[str, str]] = []
        self.peak = 0

    def run(self, brief):
        with self._lock:
            for other in self._live:
                self.overlaps.append(tuple(sorted((brief.task_id, other))))
            self._live.add(brief.task_id)
            self.peak = max(self.peak, len(self._live))
        try:
            time.sleep(self.hold_s)
            return super().run(brief)
        finally:
            with self._lock:
                self._live.discard(brief.task_id)


def test_two_tasks_claiming_one_path_are_never_live_at_the_same_moment():
    """The unit test asserts a deferral event. This asserts the thing the
    deferral exists to prevent, with real threads and real overlap."""
    claims = {f"t{i}": [f"src/area{i % 3}/**"] for i in range(12)}
    tasks = [
        Task(id=tid, name=tid, role="backend", ownership=claim)
        for tid, claim in claims.items()
    ]
    worker = _OverlapDetector()
    state = _run(tasks, worker)
    assert state.succeeded()
    for a, b in worker.overlaps:
        assert not _own.conflicts(claims[a], claims[b]), (
            f"{a} and {b} overlapped while claiming intersecting paths"
        )


def test_independent_tasks_actually_run_concurrently():
    """A scheduler that never overlaps anything passes every ownership test and
    is worth nothing."""
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="backend", ownership=[f"src/area{i}/**"])
        for i in range(8)
    ]
    worker = _OverlapDetector(hold_s=0.05)
    _run(tasks, worker, spec=_spec(budget=RunBudget(max_parallel=4)))
    assert worker.peak > 1, "nothing ever ran in parallel"


def test_max_parallel_is_never_exceeded():
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="backend", ownership=[f"src/area{i}/**"])
        for i in range(12)
    ]
    worker = _OverlapDetector(hold_s=0.03)
    _run(tasks, worker, spec=_spec(budget=RunBudget(max_parallel=3)))
    assert worker.peak <= 3


def test_read_only_tasks_pack_freely():
    tasks = [
        Task(id=f"r{i}", name=f"r{i}", role="review", writes=False) for i in range(6)
    ]
    worker = _OverlapDetector(hold_s=0.05)
    _run(tasks, worker, spec=_spec(budget=RunBudget(max_parallel=4)))
    assert worker.peak > 1, "read-only tasks hold no claim and should pack"


# --- failure injection ----------------------------------------------------


class _Flaky(MockWorker):
    """Fails a deterministic fraction of attempts.

    The decision is a pure function of (seed, task id, attempt number) rather
    than a draw from one shared generator. A shared generator is consumed in
    thread-completion order, so the same seed would produce different failures
    on different interleavings — which would make the determinism test below
    assert something about the thread scheduler instead of about vise.
    """

    def __init__(self, fail_rate: float, seed: int, kind=FailureKind.CODE_BUG, **kw):
        super().__init__(**kw)
        self.seed = seed
        self.fail_rate = fail_rate
        self.kind = kind
        self.attempts = 0
        self._lock = threading.Lock()
        self._seen: dict[str, int] = {}

    def _fails(self, task_id: str, attempt: int) -> bool:
        return random.Random(f"{self.seed}:{task_id}:{attempt}").random() < self.fail_rate

    def run(self, brief):
        with self._lock:
            self.attempts += 1
            attempt = self._seen.get(brief.task_id, 0) + 1
            self._seen[brief.task_id] = attempt
            # Recorded here rather than only on the success path: a worker that
            # forgets the briefs it failed cannot answer "did this escalate".
            self.briefs.append(brief)
        if self._fails(brief.task_id, attempt):
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.FAIL, summary="injected failure",
                classification=self.kind, usage=Usage(cost_usd=0.01),
                model=brief.model, effort=brief.effort,
            )
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.PASS, summary="ok",
            evidence="$ mock\nok", checks="$ mock\nok",
            usage=Usage(cost_usd=0.01), model=brief.model, effort=brief.effort,
        )


@pytest.mark.parametrize("seed", [5, 13, 29])
def test_a_third_of_attempts_failing_still_terminates(seed):
    """Every exit condition is guarded by something a retry resets, so the loop
    terminating is a claim worth testing rather than assuming."""
    state = _run(_graph(25, seed), _Flaky(0.33, seed))
    assert state.is_done()
    for record in state.tasks.values():
        assert record.state in TERMINAL_OR_PARKED


def test_every_attempt_failing_terminates_rather_than_looping():
    state = _run(_graph(10, 1), _Flaky(1.0, 1))
    assert state.is_done()
    assert not state.succeeded()


def test_a_task_never_exceeds_its_attempt_budget():
    config = SchedulerConfig(max_attempts=3)
    state = _run(_graph(8, 2), _Flaky(1.0, 2), config=config)
    for record in state.tasks.values():
        assert record.attempt_count <= 3, f"{record.task_id} ran {record.attempt_count} times"


def test_injected_environment_failures_do_not_climb_the_ladder():
    worker = _Flaky(1.0, 3, kind=FailureKind.ENVIRONMENT_BUG)
    tasks = [Task(id="t0", name="t0", role="backend", ownership=["src/**"])]
    _run(tasks, worker)
    models = {f"{b.model}/{b.effort}" for b in worker.briefs}
    assert models == {"sonnet/medium"}, "an environment failure must not escalate"


def test_a_worker_that_always_raises_terminates_the_run():
    class Exploding:
        def run(self, brief):
            raise RuntimeError("always")

    state = _run(_graph(6, 4), Exploding())
    assert state.is_done()
    for record in state.tasks.values():
        assert record.state in TERMINAL_OR_PARKED


# --- budget under load ----------------------------------------------------


def test_a_tight_budget_stops_the_run_and_is_never_exceeded():
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="backend", ownership=[f"src/area{i}/**"])
        for i in range(20)
    ]
    worker = MockWorker(scripted={
        f"t{i}": [TaskResult(task_id=f"t{i}", verdict=Verdict.PASS, summary="ok",
                             evidence="e", checks="c", usage=Usage(cost_usd=0.85))]
        for i in range(20)
    })
    state = _run(tasks, worker, spec=_spec(budget=RunBudget(max_cost_usd=3.0, max_parallel=4)))
    assert state.human_gate
    assert state.ledger.spent.cost_usd <= 3.0 + 0.85, "at most one task's overshoot"
    assert not state.succeeded()


# --- determinism ----------------------------------------------------------


def test_the_same_seed_produces_the_same_outcome():
    """A stress test that fails one run in twenty teaches people to re-run it."""
    first = _run(_graph(20, 8), _Flaky(0.4, 8))
    second = _run(_graph(20, 8), _Flaky(0.4, 8))
    assert {k: v.state for k, v in first.tasks.items()} == \
        {k: v.state for k, v in second.tasks.items()}


def test_state_survives_a_round_trip_through_disk_under_load(tmp_path):
    from vise.runtime.state import RunState

    state = _run(_graph(15, 9), _Flaky(0.3, 9), state_root=tmp_path)
    reloaded = RunState.load(tmp_path, state.spec.run_id)
    assert reloaded is not None
    assert {k: v.state for k, v in reloaded.tasks.items()} == \
        {k: v.state for k, v in state.tasks.items()}
    assert reloaded.ledger.spent.cost_usd == pytest.approx(state.ledger.spent.cost_usd)


def test_a_stopped_run_never_leaves_a_task_pending():
    """Found by the 40-task stress run, intermittently: a task collected after
    the run had already stopped for a person wrote PENDING back over the state
    the stop had put it in, leaving a run that reported itself done with a task
    waiting to start."""
    worker = _Flaky(1.0, 17, kind=FailureKind.ENVIRONMENT_BUG)
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="backend", ownership=[f"src/area{i}/**"])
        for i in range(8)
    ]
    state = _run(tasks, worker, spec=_spec(budget=RunBudget(max_parallel=4)))
    assert state.is_done()
    live = [r.task_id for r in state.tasks.values()
            if r.state in (TaskState.PENDING, TaskState.READY, TaskState.RUNNING)]
    assert not live, f"is_done() is true with {live} still live"


def test_a_cancelled_run_parks_everything_it_could_not_finish():
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="backend", ownership=[f"src/area{i}/**"])
        for i in range(10)
    ]
    config = SchedulerConfig(should_cancel=lambda: True)
    state = _run(tasks, _OverlapDetector(hold_s=0.01), config=config)
    assert all(r.state is TaskState.CANCELLED for r in state.tasks.values())
