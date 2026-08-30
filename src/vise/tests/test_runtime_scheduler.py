"""The dispatch loop, driven entirely by mocks.

Every test here is a claim about behaviour a real run depends on and nobody
would notice going wrong: that a failed dependency does not release its
dependents, that two tasks claiming one path never run together, that an
escalation actually reaches the worker as a bigger model, that a run out of
budget stops instead of finishing cheaply.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_engine import Task
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
from vise.runtime.scheduler import Scheduler, SchedulerConfig, new_run_id, run_tasks
from vise.runtime.state import RunState
from vise.runtime.worker import MockWorker


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d", model="sonnet",
                  capabilities=("backend", "python")),
        AgentSpec(id="frontend", role="frontend", description="d", model="sonnet",
                  capabilities=("frontend",)),
        AgentSpec(id="tester", role="test", description="d", model="sonnet",
                  capabilities=("test",)),
        AgentSpec(id="reviewer", role="review", description="d", model="opus",
                  writes=False, capabilities=("review",)),
    ):
        reg.agents[spec.id] = spec
    return reg


def _spec(**kw) -> RunSpec:
    base = dict(run_id="r1", goal="g", project_dir="/nonexistent-not-a-repo",
                budget=RunBudget(max_parallel=4))
    base.update(kw)
    return RunSpec(**base)


def _run(tasks, worker=None, **kw) -> RunState:
    kw.setdefault("registry", _registry())
    spec = kw.pop("spec", _spec())
    return Scheduler(worker=worker or MockWorker(), **kw).run(spec, tasks)


def _fail(task_id, classification=FailureKind.CODE_BUG, summary="wrong") -> TaskResult:
    return TaskResult(task_id=task_id, verdict=Verdict.FAIL, summary=summary,
                      classification=classification, usage=Usage(cost_usd=0.1))


def _pass(task_id, **kw) -> TaskResult:
    base = dict(task_id=task_id, verdict=Verdict.PASS, summary="ok",
                evidence="$ pytest\nok", checks="$ ruff\nok", usage=Usage(cost_usd=0.1))
    base.update(kw)
    return TaskResult(**base)


# --- the happy path -------------------------------------------------------


def test_a_clean_run_succeeds_and_respects_dependencies():
    worker = MockWorker()
    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/a/**"]),
        Task(id="b", name="b", role="frontend", ownership=["web/**"], dependencies=["a"]),
    ]
    state = _run(tasks, worker)
    assert state.succeeded()
    assert [b.task_id for b in worker.briefs] == ["a", "b"]


def test_an_empty_task_list_finishes_without_claiming_success():
    """Nothing ran, so nothing succeeded. `all([])` would say otherwise."""
    state = _run([])
    assert not state.succeeded()
    assert state.is_done()


def test_the_run_records_which_agent_and_model_each_task_used():
    state = _run([Task(id="a", name="a", role="review", writes=False)])
    record = state.tasks["a"]
    assert record.agent_id == "reviewer"
    assert (record.model, record.effort) == ("opus", "high")


# --- failure does not release dependents ---------------------------------


def test_a_failed_dependency_blocks_its_dependents_rather_than_releasing_them():
    """Letting a dependent start on failed work makes the failure resurface
    later wearing the dependent's name."""
    worker = MockWorker(scripted={"a": [_fail("a")] * 6})
    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/a/**"]),
        Task(id="b", name="b", role="frontend", ownership=["web/**"], dependencies=["a"]),
    ]
    state = _run(tasks, worker)
    assert state.tasks["b"].state in (TaskState.BLOCKED, TaskState.WAITING_HUMAN)
    assert "b" not in {br.task_id for br in worker.briefs}


def test_a_blocked_task_names_the_dependency_that_blocked_it():
    worker = MockWorker(scripted={"a": [_fail("a")] * 6})
    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/a/**"]),
        Task(id="b", name="b", role="frontend", ownership=["web/**"], dependencies=["a"]),
    ]
    state = _run(tasks, worker)
    assert "a" in state.tasks["b"].note


# --- escalation reaches the worker ---------------------------------------


def test_a_failing_task_is_retried_on_a_bigger_model():
    worker = MockWorker(scripted={"a": [_fail("a"), _fail("a"), _pass("a")]})
    state = _run([Task(id="a", name="a", role="backend", ownership=["src/**"])], worker)
    models = [b.model + "/" + b.effort for b in worker.briefs]
    assert models == ["sonnet/medium", "sonnet/high", "opus/high"]
    assert state.tasks["a"].state is TaskState.SUCCEEDED


def test_each_retry_carries_the_previous_attempts_into_the_brief():
    worker = MockWorker(scripted={"a": [_fail("a", summary="parser ate a digit"), _pass("a")]})
    _run([Task(id="a", name="a", role="backend", ownership=["src/**"])], worker)
    second = worker.briefs[1]
    assert len(second.attempts) == 1
    assert "parser ate a digit" in second.render()
    assert "already tried, do not repeat" in second.render()


def test_an_environment_failure_retries_at_the_same_rung():
    worker = MockWorker(scripted={
        "a": [_fail("a", FailureKind.ENVIRONMENT_BUG), _pass("a")]
    })
    _run([Task(id="a", name="a", role="backend", ownership=["src/**"])], worker)
    assert [b.model + "/" + b.effort for b in worker.briefs] == \
        ["sonnet/medium", "sonnet/medium"]


def test_a_task_that_exhausts_the_ladder_stops_for_a_person():
    worker = MockWorker(scripted={"a": [_fail("a")] * 8})
    state = _run([Task(id="a", name="a", role="backend", ownership=["src/**"])], worker)
    assert state.human_gate
    assert state.tasks["a"].state in (TaskState.FAILED, TaskState.WAITING_HUMAN)


# --- ownership ------------------------------------------------------------


def test_two_tasks_claiming_one_path_never_run_together():
    seen: list[set[str]] = []

    class Watcher(MockWorker):
        def run(self, brief):
            seen.append(set(inflight))
            return super().run(brief)

    inflight: set[str] = set()
    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/auth/**"]),
        Task(id="b", name="b", role="backend", ownership=["src/**"]),
    ]
    state = _run(tasks, Watcher())
    assert state.succeeded()
    # Both ran, and the deferral is on the record.
    assert any(e["kind"] == "deferred" for e in state.events)


def test_a_read_only_task_holds_no_claim_and_is_never_deferred():
    tasks = [
        Task(id="wide", name="wide", role="backend", ownership=["src/**"]),
        Task(id="look", name="look", role="review", writes=False),
    ]
    state = _run(tasks)
    assert state.succeeded()
    assert not [e for e in state.events if e["kind"] == "deferred"]


# --- budget ---------------------------------------------------------------


def test_a_run_out_of_budget_stops_rather_than_finishing_cheaply():
    """One opus task actually costs $2.10; the second cannot fit under $2.50."""
    tasks = [Task(id=f"t{i}", name=f"t{i}", role="review", writes=False) for i in range(4)]
    worker = MockWorker(scripted={
        f"t{i}": [_pass(f"t{i}", usage=Usage(cost_usd=2.10))] for i in range(4)
    })
    state = _run(tasks, worker, spec=_spec(budget=RunBudget(max_cost_usd=2.5, max_parallel=1)))
    assert state.human_gate
    assert "remaining" in state.human_gate or "ceiling" in state.human_gate
    assert not state.succeeded()


def test_in_flight_reservations_bind_before_any_cost_has_settled():
    """Checking admission against settled spend alone lets four opus tasks start
    against a budget for one: nothing is billed yet, so everything fits."""
    started: list[str] = []

    class Slow(MockWorker):
        def run(self, brief):
            started.append(brief.task_id)
            return _pass(brief.task_id, usage=Usage(cost_usd=2.10))

    tasks = [Task(id=f"t{i}", name=f"t{i}", role="review", writes=False) for i in range(4)]
    state = _run(tasks, Slow(), spec=_spec(budget=RunBudget(max_cost_usd=2.5, max_parallel=4)))
    assert len(started) == 1, "the second task must not start against a reserved budget"
    assert state.human_gate


def test_the_worker_ceiling_stops_the_run():
    tasks = [Task(id=f"t{i}", name=f"t{i}", role="review", writes=False) for i in range(4)]
    state = _run(tasks, spec=_spec(budget=RunBudget(max_workers=2, max_parallel=1)))
    assert state.human_gate
    assert state.ledger.workers_started <= 2


def test_max_parallel_bounds_what_is_in_flight():
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="frontend", ownership=[f"web/{i}/**"])
        for i in range(6)
    ]
    state = _run(tasks, spec=_spec(budget=RunBudget(max_parallel=2)))
    assert state.succeeded()


# --- unroutable -----------------------------------------------------------


def test_a_task_with_no_role_is_blocked_not_guessed_at():
    state = _run([Task(id="a", name="a")])
    assert state.tasks["a"].state is TaskState.BLOCKED
    assert "no role" in state.tasks["a"].note


def test_a_task_whose_role_nobody_takes_is_blocked():
    state = _run([Task(id="a", name="a", role="astrology")])
    assert state.tasks["a"].state is TaskState.BLOCKED


# --- worker crash ---------------------------------------------------------


def test_a_worker_that_raises_fails_the_task_not_the_run():
    class Exploding:
        def __init__(self):
            self.calls = 0

        def run(self, brief):
            self.calls += 1
            if brief.task_id == "a" and self.calls == 1:
                raise RuntimeError("boom")
            return _pass(brief.task_id)

    state = _run([Task(id="a", name="a", role="backend", ownership=["src/**"])], Exploding())
    assert state.tasks["a"].state is TaskState.SUCCEEDED
    assert state.tasks["a"].attempts[0].verdict is Verdict.INCONCLUSIVE


# --- cancellation ---------------------------------------------------------


def test_a_cancelled_run_stops_and_marks_the_rest_cancelled():
    tasks = [Task(id=f"t{i}", name=f"t{i}", role="frontend", ownership=[f"web/{i}/**"])
             for i in range(3)]
    config = SchedulerConfig(should_cancel=lambda: True)
    state = _run(tasks, config=config)
    assert state.cancelled
    assert all(t.state is TaskState.CANCELLED for t in state.tasks.values())


# --- replanning -----------------------------------------------------------


def test_a_spec_bug_with_no_replanner_stops_for_a_person():
    worker = MockWorker(scripted={"a": [_fail("a", FailureKind.SPEC_BUG)] * 4})
    state = _run([Task(id="a", name="a", role="backend", ownership=["src/**"])], worker)
    assert state.human_gate
    assert any(e["kind"] == "replan_unavailable" for e in state.events)


def test_a_replanner_replaces_the_task_graph_and_keeps_succeeded_work():
    worker = MockWorker(scripted={"a": [_fail("a", FailureKind.SPEC_BUG)]})

    def replanner(state, original):
        return [
            Task(id="done", name="done", role="backend", ownership=["src/done/**"]),
        ]

    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/a/**"]),
        Task(id="kept", name="kept", role="frontend", ownership=["web/**"]),
    ]
    state = _run(tasks, worker, config=SchedulerConfig(replanner=replanner))
    assert state.replans == 1
    assert state.tasks["kept"].state is TaskState.SUCCEEDED, "verified work survives a replan"
    assert state.tasks["done"].state is TaskState.SUCCEEDED


def test_a_replanner_that_declines_stops_for_a_person():
    worker = MockWorker(scripted={"a": [_fail("a", FailureKind.SPEC_BUG)] * 4})
    state = _run(
        [Task(id="a", name="a", role="backend", ownership=["src/**"])],
        worker,
        config=SchedulerConfig(replanner=lambda s, t: None),
    )
    assert state.human_gate
    assert any(e["kind"] == "replan_declined" for e in state.events)


# --- artifacts ------------------------------------------------------------


def test_artifacts_a_worker_produces_reach_the_next_task(tmp_path: Path):
    store = ArtifactStore(tmp_path, "r1")
    worker = MockWorker(scripted={
        "a": [_pass("a", artifacts=(Artifact("r1", "a", "research", {"finding": 42}),))],
    })
    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/a/**"]),
        Task(id="b", name="b", role="frontend", ownership=["web/**"], dependencies=["a"]),
    ]
    _run(tasks, worker, artifacts=store)
    downstream = [b for b in worker.briefs if b.task_id == "b"][0]
    assert [a.kind for a in downstream.inputs] == ["research"]
    assert downstream.inputs[0].payload == {"finding": 42}


# --- persistence ----------------------------------------------------------


def test_the_run_state_is_written_and_reads_back(tmp_path: Path):
    tasks = [Task(id="a", name="a", role="backend", ownership=["src/**"])]
    state = _run(tasks, state_root=tmp_path)
    reloaded = RunState.load(tmp_path, state.spec.run_id)
    assert reloaded is not None
    assert reloaded.tasks["a"].state is TaskState.SUCCEEDED
    assert reloaded.spec.goal == state.spec.goal
    assert reloaded.events


def test_the_state_file_exists_while_the_first_task_is_still_running(tmp_path: Path):
    """Found by watching a real run.

    The file used to be written on the first *collection*, so for the whole of
    the first task `vise runtime status` answered "no such run" — the one moment
    someone who just started a run looks at it — and a process killed in that
    window left nothing to say what had been dispatched.
    """
    seen: dict[str, object] = {}

    class Watcher:
        def run(self, brief):
            mid = RunState.load(tmp_path, "r1")
            seen["exists"] = mid is not None
            if mid is not None:
                seen["state"] = mid.tasks["a"].state
                seen["model"] = mid.tasks["a"].model
            return MockWorker().run(brief)

    _run([Task(id="a", name="a", role="backend", ownership=["src/**"])],
         worker=Watcher(), state_root=tmp_path)

    assert seen["exists"] is True
    assert seen["state"] is TaskState.RUNNING
    assert seen["model"] == "sonnet"


def test_per_task_spend_survives_a_reload(tmp_path: Path):
    """Found by running `vise runtime budget` on a real run.

    It printed its "per task:" heading over nothing. The command reads only
    persisted state, so a by_task that did not survive the round trip made it
    unable to answer the one question it exists for, for every run.
    """
    tasks = [Task(id="a", name="a", role="backend", ownership=["src/**"])]
    state = _run(tasks, state_root=tmp_path)
    assert state.ledger.by_task, "the live ledger should attribute spend per task"

    reloaded = RunState.load(tmp_path, state.spec.run_id)
    assert reloaded is not None
    assert set(reloaded.ledger.by_task) == set(state.ledger.by_task)
    assert reloaded.ledger.report()["by_task"]["a"]["cost_usd"] == \
        state.ledger.by_task["a"].cost_usd


def test_loading_an_unknown_run_returns_none(tmp_path: Path):
    assert RunState.load(tmp_path, "never-ran") is None


# --- misc -----------------------------------------------------------------


def test_run_ids_are_unique():
    assert new_run_id() != new_run_id()


def test_run_tasks_builds_a_spec_when_none_is_given():
    state = run_tasks(
        [Task(id="a", name="a", role="backend", ownership=["src/**"])],
        worker=MockWorker(),
        goal="ship it",
        project_dir="/nonexistent-not-a-repo",
        registry=_registry(),
    )
    assert state.spec.goal == "ship it"
    assert state.succeeded()


@pytest.mark.parametrize("kind", ["run_started", "dispatched", "collected", "run_finished"])
def test_the_event_log_narrates_the_run(kind):
    state = _run([Task(id="a", name="a", role="backend", ownership=["src/**"])])
    assert any(e["kind"] == kind for e in state.events)
