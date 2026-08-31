"""The ledger's invariant, asserted over every way a run can end.

The audit found the cost of in-flight work discarded on a stop: the ledger
reported $0.00 for money that was really spent. The fix for that one path is a
fix for one path. What keeps it fixed is this file, which does not name a path
at all — it asserts the property that has to hold whichever way the loop leaves.

Two claims, and they are different:

* **Conservation.** Every dollar a worker reported is a dollar the ledger
  reports. Not "roughly", not "for the tasks that were collected".
* **No dangling reservation.** A reservation is an estimate held against the
  budget while a task is in flight. A run that ends still holding one has
  fenced off money nobody will ever spend, and the next run computing
  ``available`` from that file sees a budget smaller than it is.

The scenarios below are the ways a run ends that anyone has thought of. The
value of the file is that adding another one is a five-line diff, and a new
stop path that forgets to settle fails here rather than in someone's invoice.
"""
from __future__ import annotations

import threading

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import (
    RunBudget,
    RunSpec,
    TaskResult,
    Usage,
    Verdict,
)
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import (
    WAIT_SLICE_S,
    Scheduler,
    SchedulerConfig,
)


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d",
                  model="sonnet", capabilities=("backend", "python")),
        AgentSpec(id="tester", role="test", description="d", model="sonnet",
                  capabilities=("test",)),
    ):
        reg.agents[spec.id] = spec
    return reg


class _AccountingWorker:
    """Reports a fixed cost per call and keeps its own books.

    ``self.billed`` is the ground truth the ledger is measured against: what a
    provider would have charged. Every ``run`` that returns adds to it, whether
    or not anyone ever reads the result.
    """

    def __init__(self, cost_usd: float = 1.25, verdict: Verdict = Verdict.PASS,
                 gate: threading.Event | None = None, raises: bool = False):
        self.cost_usd = cost_usd
        self.verdict = verdict
        self.gate = gate
        self.raises = raises
        self.started = threading.Event()
        self.billed = 0.0
        self._lock = threading.Lock()

    def run(self, brief):
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if self.raises:
            raise RuntimeError("the worker died mid-flight")
        with self._lock:
            self.billed += self.cost_usd
        return TaskResult(
            task_id=brief.task_id, verdict=self.verdict,
            summary="done", evidence="$ pytest\n1 passed",
            checks="$ ruff\nAll checks passed!",
            usage=Usage(cost_usd=self.cost_usd, tokens_in=10, tokens_out=20),
        )


def _run(tasks, worker, *, budget=None, config=None):
    spec = RunSpec(run_id="r1", goal="g", project_dir="/nonexistent-not-a-repo",
                   budget=budget or RunBudget(max_parallel=2))
    return Scheduler(
        worker=worker, registry=_registry(), config=config or SchedulerConfig(),
    ).run(spec, tasks)


def _release_once_started(worker):
    """A `should_cancel` that never cancels — it only unblocks the worker."""
    calls = []

    def hook():
        calls.append(1)
        if len(calls) > 1 and worker.gate is not None:
            worker.gate.set()
        return False

    return hook


# --- the scenarios --------------------------------------------------------


def _clean_pass():
    w = _AccountingWorker()
    return w, _run([Task(id="a", name="A", role="backend")], w)


def _two_tasks_in_parallel():
    w = _AccountingWorker()
    return w, _run(
        [Task(id="a", name="A", role="backend", ownership=["src/a/**"]),
         Task(id="b", name="B", role="backend", ownership=["src/b/**"])], w)


def _a_dependency_chain():
    w = _AccountingWorker()
    return w, _run(
        [Task(id="a", name="A", role="backend"),
         Task(id="b", name="B", role="test", dependencies=["a"])], w)


def _a_failing_worker():
    w = _AccountingWorker(verdict=Verdict.FAIL)
    return w, _run([Task(id="a", name="A", role="backend")], w)


def _a_worker_that_raises():
    w = _AccountingWorker(raises=True)
    return w, _run([Task(id="a", name="A", role="backend")], w)


def _cancelled_in_flight():
    gate = threading.Event()
    w = _AccountingWorker(gate=gate)

    def hook():
        if w.started.wait(timeout=5):
            gate.set()
            return True
        return False

    return w, _run([Task(id="a", name="A", role="backend")], w,
                   config=SchedulerConfig(should_cancel=hook))


def _stopped_by_the_wall_clock():
    gate = threading.Event()
    w = _AccountingWorker(gate=gate)
    return w, _run(
        [Task(id="a", name="A", role="backend")], w,
        budget=RunBudget(max_parallel=1, max_wall_time_s=WAIT_SLICE_S / 2),
        config=SchedulerConfig(should_cancel=_release_once_started(w)),
    )


def _stopped_by_the_budget():
    w = _AccountingWorker(cost_usd=5.0)
    return w, _run(
        [Task(id="a", name="A", role="backend"),
         Task(id="b", name="B", role="backend", dependencies=["a"]),
         Task(id="c", name="C", role="backend", dependencies=["b"])], w,
        budget=RunBudget(max_parallel=1, max_cost_usd=6.0))


SCENARIOS = {
    "a clean pass": _clean_pass,
    "two tasks in parallel": _two_tasks_in_parallel,
    "a dependency chain": _a_dependency_chain,
    "a failing worker": _a_failing_worker,
    "a worker that raises": _a_worker_that_raises,
    "cancelled in flight": _cancelled_in_flight,
    "stopped by the wall clock": _stopped_by_the_wall_clock,
    "stopped by the budget": _stopped_by_the_budget,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_ledger_reports_every_dollar_a_worker_billed(name):
    worker, state = SCENARIOS[name]()
    assert state.ledger.spent.cost_usd == pytest.approx(worker.billed), (
        f"{name}: the worker billed {worker.billed} and the ledger reports "
        f"{state.ledger.spent.cost_usd}"
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_run_ends_holding_a_reservation(name):
    _worker, state = SCENARIOS[name]()
    assert state.ledger.reserved == {}, (
        f"{name}: ended holding {state.ledger.reserved}"
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_task_that_billed_carries_an_attempt(name):
    """Cost with no attempt behind it is cost nobody can attribute."""
    worker, state = SCENARIOS[name]()
    if worker.billed == 0:
        return
    attempts = sum(r.attempt_count for r in state.tasks.values())
    assert attempts > 0, f"{name}: billed {worker.billed} across zero attempts"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_persisted_ledger_agrees_with_the_live_one(name):
    """`vise runtime status` reads the file, not the object in memory."""
    _worker, state = SCENARIOS[name]()
    report = state.ledger.report()
    assert report["spent"]["cost_usd"] == pytest.approx(state.ledger.spent.cost_usd)
    assert report["reserved_usd"] == pytest.approx(0.0)
    attributed = sum(u["cost_usd"] for u in report["by_task"].values())
    assert attributed == pytest.approx(state.ledger.spent.cost_usd), (
        "every dollar in the total has to be attributable to a task"
    )
