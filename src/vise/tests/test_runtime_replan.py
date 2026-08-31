"""The loop closing — and, more importantly, the loop stopping.

`recovery.decide` could always return REPLAN and the scheduler could always swap
a task graph. Nothing was on the other end of `config.replanner`, so in
production a spec or architecture failure ended at `stop_for_human`. Wiring it
turns vise from plan → execute → stop into plan → execute → observe → replan →
execute.

That is also why half this file is about termination. A static plan runs each
broken thing once; a loop runs it until something stops it. The tests that matter
most here are the ones asserting that a run which cannot be fixed by replanning
ends anyway — bounded, with its cost settled, and saying why.
"""
from __future__ import annotations

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
from vise.runtime.recovery import DEFAULT_MAX_REPLANS
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.replan import REMEDIATION_SUFFIX, default_replanner
from vise.runtime.scheduler import Scheduler, SchedulerConfig


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d",
                  model="sonnet", capabilities=("backend", "python")),
        AgentSpec(id="designer", role="design", description="d", model="opus",
                  writes=False, capabilities=("design",)),
    ):
        reg.agents[spec.id] = spec
    return reg


class _ScriptedWorker:
    """Fails with a given classification until told otherwise."""

    def __init__(self, classification=FailureKind.SPEC_BUG, heal_after_design=False,
                 fails=("a",)):
        self.classification = classification
        self.heal_after_design = heal_after_design
        self.fails = set(fails)
        self.seen: list[str] = []
        self.designed = False

    def run(self, brief):
        self.seen.append(brief.task_id)
        if brief.role == "design":
            self.designed = True
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS,
                summary="the criteria should have been X",
                evidence="$ read spec\nok", checks="$ ruff\nAll checks passed!",
                usage=Usage(cost_usd=0.5),
            )
        if brief.task_id not in self.fails:
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS, summary="done",
                evidence="$ pytest\n1 passed",
                checks="$ ruff\nAll checks passed!", usage=Usage(cost_usd=1.0),
            )
        if self.heal_after_design and self.designed:
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS, summary="done",
                evidence="$ pytest\n1 passed",
                checks="$ ruff\nAll checks passed!", usage=Usage(cost_usd=1.0),
            )
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.FAIL,
            summary="the acceptance criteria describe a different feature",
            classification=self.classification, usage=Usage(cost_usd=1.0),
        )


def _run(tasks, worker, **kw):
    spec = RunSpec(run_id="r1", goal="g", project_dir="/nonexistent-not-a-repo",
                   budget=RunBudget(max_parallel=2))
    kw.setdefault("registry", _registry())
    return Scheduler(worker=worker, **kw).run(spec, tasks)


# --- the hook is actually attached ---------------------------------------


def test_the_scheduler_ships_with_a_replanner_attached():
    """The regression this file exists for: the hook was never wired."""
    assert SchedulerConfig().replanner is default_replanner


def test_a_spec_bug_produces_a_design_task_ahead_of_the_failed_one():
    worker = _ScriptedWorker(heal_after_design=True)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    remediation = f"a{REMEDIATION_SUFFIX}"
    assert remediation in state.tasks, (
        f"no remediation task was planned: {sorted(state.tasks)}"
    )
    last_retry = len(worker.seen) - 1 - worker.seen[::-1].index("a")
    assert worker.seen.index(remediation) < last_retry, (
        f"the design task must run before the retry it informs: {worker.seen}"
    )


def test_the_retry_after_a_replan_can_succeed():
    worker = _ScriptedWorker(heal_after_design=True)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    assert state.tasks["a"].state is TaskState.SUCCEEDED
    assert state.replans == 1


def test_an_architecture_bug_replans_too():
    worker = _ScriptedWorker(classification=FailureKind.ARCHITECTURE_BUG,
                             heal_after_design=True)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    assert state.replans == 1
    assert state.tasks["a"].state is TaskState.SUCCEEDED


@pytest.mark.parametrize("kind", [FailureKind.CODE_BUG, FailureKind.TEST_BUG,
                                  FailureKind.ENVIRONMENT_BUG])
def test_a_failure_that_is_not_about_the_plan_never_replans(kind):
    """Replanning a code bug spends a whole strategy on a typo."""
    worker = _ScriptedWorker(classification=kind)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    assert state.replans == 0
    assert not any(t.endswith(REMEDIATION_SUFFIX) for t in state.tasks)


# --- termination ---------------------------------------------------------


def test_a_run_that_cannot_be_replanned_into_health_still_ends():
    """The property a loop needs and a straight line does not.

    The worker never heals, so every attempt fails the same way. The run must
    end anyway — bounded by max_replans, not by anyone noticing.
    """
    worker = _ScriptedWorker(heal_after_design=False)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    assert state.finished_at, "the run never finished"
    assert state.replans <= DEFAULT_MAX_REPLANS
    assert state.tasks["a"].state in (TaskState.FAILED, TaskState.WAITING_HUMAN)
    assert "replan" in (state.tasks["a"].note or ""), (
        f"the run must say why it stopped, got {state.tasks['a'].note!r}"
    )
    assert state.ledger.reserved == {}, "and it must not end holding money"


def test_the_same_task_is_never_given_two_remediations():
    """The id is derived from the task, so the second attempt finds it present."""
    worker = _ScriptedWorker(heal_after_design=False)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    remediations = [t for t in state.tasks if t.endswith(REMEDIATION_SUFFIX)]
    assert len(remediations) == 1, remediations


def test_a_replan_with_nothing_to_change_declines():
    """Declining is a real answer; the scheduler stops for a person on it."""
    from vise.runtime.state import RunState

    spec = RunSpec(run_id="r", goal="g", project_dir=".", budget=RunBudget())
    state = RunState(spec=spec)
    state.record("a")

    assert default_replanner(state, [Task(id="a", name="A", role="backend")]) is None


def test_a_replan_does_not_lose_the_money_already_spent():
    worker = _ScriptedWorker(heal_after_design=True)
    state = _run([Task(id="a", name="A", role="backend")], worker)

    assert state.ledger.spent.cost_usd > 0
    assert state.ledger.reserved == {}


def test_succeeded_work_survives_a_replan():
    """Redoing a task that passed is paying twice for the same answer."""
    worker = _ScriptedWorker(heal_after_design=True, fails=("a",))
    state = _run(
        [Task(id="done", name="Done", role="backend", ownership=["src/x/**"]),
         Task(id="a", name="A", role="backend", ownership=["src/y/**"])],
        worker,
    )

    assert state.tasks["done"].state is TaskState.SUCCEEDED
    assert state.tasks["done"].attempt_count == 1, (
        "a task that already passed was re-run by the replan"
    )


# --- the boundary that makes any of this trustworthy ---------------------


def test_a_replanned_task_carries_no_validators_of_its_own():
    """A planner that writes the condition it is judged by grades itself.

    Every gate vise reports as `mechanical` is code someone reviewed. The moment
    a generated task can carry a generated pass condition, the distinction
    between `mechanical` and `asserted` stops meaning anything — which is the
    whole claim of the product.
    """
    from vise.runtime.state import RunState

    spec = RunSpec(run_id="r", goal="g", project_dir=".", budget=RunBudget())
    state = RunState(spec=spec)
    record = state.record("a")
    record.result = TaskResult(task_id="a", verdict=Verdict.FAIL, summary="wrong spec",
                               classification=FailureKind.SPEC_BUG)
    record.state = TaskState.FAILED

    new = default_replanner(state, [Task(id="a", name="A", role="backend")])
    remediation = next(t for t in new if t.id.endswith(REMEDIATION_SUFFIX))

    assert not getattr(remediation, "acceptance", []), (
        "the replanner invented acceptance criteria for its own task"
    )
    assert remediation.writes is False, "a remediation task reasons; it does not edit"
    assert remediation.ownership == [], "and therefore claims no ownership"


def test_the_remediation_role_is_one_the_registry_actually_staffs():
    """A task nobody can staff is a plan that stalls instead of running."""
    from vise.runtime.replan import REMEDIATION_ROLE

    assert REMEDIATION_ROLE in AgentRegistry.bundled().roles()


def test_the_remediation_task_is_priced_at_the_replan_tier():
    """POLICY prices "replan" separately because it redirects everything after it.

    Nothing dispatched a task under that role, so the row priced nobody. It is
    load-bearing now, and this is what stops it going stale again.
    """
    from vise.runtime.replan import REMEDIATION_EFFORT, REMEDIATION_MODEL
    from vise.runtime.routing import POLICY

    assert (REMEDIATION_MODEL, REMEDIATION_EFFORT) == POLICY["replan"]

    from vise.runtime.state import RunState

    spec = RunSpec(run_id="r", goal="g", project_dir=".", budget=RunBudget())
    state = RunState(spec=spec)
    record = state.record("a")
    record.result = TaskResult(task_id="a", verdict=Verdict.FAIL, summary="wrong",
                               classification=FailureKind.SPEC_BUG)
    record.state = TaskState.FAILED

    new = default_replanner(state, [Task(id="a", name="A", role="backend")])
    remediation = next(t for t in new if t.id.endswith(REMEDIATION_SUFFIX))

    assert (remediation.model, remediation.effort) == POLICY["replan"]
