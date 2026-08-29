"""The three passes above the worker: diagnose, review, roll back.

Each answers a question the worker cannot answer about itself — where its
failure lives, whether the node as a whole should ship, and whether its failed
attempt should still be sitting in the tree when the next one starts.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import (
    Artifact,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    Usage,
    Verdict,
)
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.worker import MockWorker


def _registry(*extra: AgentSpec) -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d", model="sonnet",
                  capabilities=("backend", "python")),
        *extra,
    ):
        reg.agents[spec.id] = spec
    return reg


DEBUGGER = AgentSpec(id="debugger", role="debug", description="d", model="sonnet",
                     effort="high", writes=False, capabilities=("debug",))
REVIEWER = AgentSpec(id="reviewer", role="review", description="d", model="opus",
                     effort="high", writes=False, capabilities=("review",))


def _task(**kw) -> Task:
    base = dict(id="backend-python-a", name="a", role="backend", ownership=["src/**"])
    base.update(kw)
    return Task(**base)


def _run(tasks, worker, registry=None, **kw):
    spec = kw.pop("spec", RunSpec(run_id="r", goal="ship it",
                                  project_dir="/nonexistent-not-a-repo",
                                  budget=RunBudget(max_parallel=2)))
    return Scheduler(worker=worker, registry=registry or _registry(), **kw).run(spec, tasks)


# --- the debugger ---------------------------------------------------------


class _Undiagnosed(MockWorker):
    """Fails without saying why, and answers a debugger from a script."""

    def __init__(self, kind: str | None, **kw):
        super().__init__(**kw)
        self.kind = kind
        self.roles: list[str] = []

    def run(self, brief):
        self.briefs.append(brief)
        self.roles.append(brief.role)
        if brief.role == "debug":
            payload = {"classification": self.kind} if self.kind else {}
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS,
                artifacts=(Artifact(brief.run_id, brief.task_id, "finding", payload),),
                usage=Usage(cost_usd=0.2),
            )
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.FAIL,
            summary="it broke", evidence="AssertionError: expected 3, got 4",
            usage=Usage(cost_usd=0.1), model=brief.model, effort=brief.effort,
        )


def test_an_undiagnosed_failure_is_sent_to_a_debugger():
    worker = _Undiagnosed("environment_bug")
    _run([_task()], worker, _registry(DEBUGGER))
    assert "debug" in worker.roles


def test_the_debuggers_classification_decides_what_happens_next():
    """environment_bug retries at the same rung; without it this would escalate."""
    worker = _Undiagnosed("environment_bug")
    _run([_task()], worker, _registry(DEBUGGER))
    work = [b for b in worker.briefs if b.role == "backend"]
    assert [f"{b.model}/{b.effort}" for b in work[:2]] == ["sonnet/medium", "sonnet/medium"]


def test_a_spec_bug_from_the_debugger_triggers_a_replan_not_an_escalation():
    worker = _Undiagnosed("spec_bug")
    state = _run([_task()], worker, _registry(DEBUGGER))
    assert any(e["kind"] == "replan_unavailable" for e in state.events)


def test_a_failure_that_names_its_own_kind_is_not_sent_to_a_debugger():
    """Most failures name themselves, and a model call to confirm is waste."""
    worker = MockWorker(scripted={"backend-python-a": [
        TaskResult(task_id="backend-python-a", verdict=Verdict.FAIL, summary="x",
                   classification=FailureKind.CODE_BUG, usage=Usage(cost_usd=0.1))
    ] * 6})
    state = _run([_task()], worker, _registry(DEBUGGER))
    assert not any(e["kind"] == "diagnosing" for e in state.events)


def test_a_failure_the_text_heuristic_recognises_is_not_sent_to_a_debugger():
    worker = MockWorker(scripted={"backend-python-a": [
        TaskResult(task_id="backend-python-a", verdict=Verdict.FAIL,
                   summary="bash: psql: command not found", usage=Usage(cost_usd=0.1))
    ] * 6})
    state = _run([_task()], worker, _registry(DEBUGGER))
    assert not any(e["kind"] == "diagnosing" for e in state.events)


def test_no_debug_agent_means_no_diagnosis_and_no_crash():
    state = _run([_task()], _Undiagnosed(None), _registry())
    assert state.is_done()
    assert not any(e["kind"] == "diagnosing" for e in state.events)


def test_a_debugger_that_answers_nothing_leaves_the_failure_undiagnosed():
    """The safe direction: a missing diagnosis escalates, since 'nobody said' is
    not evidence the work was fine."""
    worker = _Undiagnosed(None)
    _run([_task()], worker, _registry(DEBUGGER))
    work = [b for b in worker.briefs if b.role == "backend"]
    assert [f"{b.model}/{b.effort}" for b in work[:2]] == ["sonnet/medium", "sonnet/high"]


def test_a_debugger_that_raises_does_not_fail_the_run():
    class Exploding(_Undiagnosed):
        def run(self, brief):
            if brief.role == "debug":
                raise RuntimeError("debugger died")
            return super().run(brief)

    state = _run([_task()], Exploding(None), _registry(DEBUGGER))
    assert state.is_done()
    assert any(e["kind"] == "diagnose_failed" for e in state.events)


def test_diagnosis_is_charged_to_the_run():
    state = _run([_task()], _Undiagnosed("code_bug"), _registry(DEBUGGER))
    assert any(k.endswith("::debug") for k in state.ledger.by_task)


def test_diagnosis_can_be_switched_off():
    worker = _Undiagnosed("code_bug")
    _run([_task()], worker, _registry(DEBUGGER), config=SchedulerConfig(diagnose=False))
    assert "debug" not in worker.roles


# --- the adversarial pass -------------------------------------------------


class _Reviewing(MockWorker):
    def __init__(self, verdict: Verdict, **kw):
        super().__init__(**kw)
        self.verdict = verdict

    def run(self, brief):
        self.briefs.append(brief)
        if brief.role == "review":
            return TaskResult(
                task_id=brief.task_id, verdict=self.verdict,
                summary="the token parser accepts non-ASCII digits",
                usage=Usage(cost_usd=2.1),
            )
        return super().run(brief)


def test_the_review_runs_once_for_the_whole_node_not_once_per_task():
    worker = _Reviewing(Verdict.PASS)
    tasks = [_task(id=f"backend-python-{i}", ownership=[f"src/a{i}/**"]) for i in range(3)]
    _run(tasks, worker, _registry(REVIEWER), config=SchedulerConfig(review=True))
    assert len([b for b in worker.briefs if b.role == "review"]) == 1


def test_a_blocking_review_parks_the_run_for_a_person():
    """Deciding what to do about a shipping objection is a person's call."""
    state = _run([_task()], _Reviewing(Verdict.FAIL), _registry(REVIEWER),
                 config=SchedulerConfig(review=True))
    assert state.human_gate
    assert "non-ASCII digits" in state.human_gate


def test_a_passing_review_leaves_the_run_successful():
    state = _run([_task()], _Reviewing(Verdict.PASS), _registry(REVIEWER),
                 config=SchedulerConfig(review=True))
    assert state.succeeded()
    assert any(e["kind"] == "reviewed" for e in state.events)


def test_the_review_is_skipped_when_a_task_did_not_succeed():
    worker = _Reviewing(Verdict.PASS, scripted={"backend-python-a": [
        TaskResult(task_id="backend-python-a", verdict=Verdict.FAIL, summary="x",
                   classification=FailureKind.CODE_BUG, usage=Usage(cost_usd=0.1))
    ] * 8})
    _run([_task()], worker, _registry(REVIEWER), config=SchedulerConfig(review=True))
    assert not [b for b in worker.briefs if b.role == "review"]


def test_the_review_is_off_by_default():
    worker = _Reviewing(Verdict.FAIL)
    state = _run([_task()], worker, _registry(REVIEWER))
    assert state.succeeded()
    assert not [b for b in worker.briefs if b.role == "review"]


def test_no_review_agent_is_reported_rather_than_silently_skipped():
    state = _run([_task()], _Reviewing(Verdict.PASS), _registry(),
                 config=SchedulerConfig(review=True))
    assert any(e["kind"] == "review_unavailable" for e in state.events)


def test_a_reviewer_that_raises_does_not_fail_a_successful_run():
    class Exploding(MockWorker):
        def run(self, brief):
            if brief.role == "review":
                raise RuntimeError("reviewer died")
            return super().run(brief)

    state = _run([_task()], Exploding(), _registry(REVIEWER),
                 config=SchedulerConfig(review=True))
    assert state.succeeded()
    assert any(e["kind"] == "review_failed" for e in state.events)


# --- rollback -------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "seed.txt").write_text("s\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


def test_a_failed_attempts_worktree_is_discarded_so_the_next_starts_clean(tmp_path):
    repo = _repo(tmp_path)
    trees: list[str] = []

    class Failing(MockWorker):
        def run(self, brief):
            self.briefs.append(brief)
            trees.append(brief.workdir)
            (Path(brief.workdir) / "junk.py").write_text("junk\n", encoding="utf-8")
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.FAIL, summary="x",
                classification=FailureKind.CODE_BUG, usage=Usage(cost_usd=0.1),
                model=brief.model, effort=brief.effort,
            )

    spec = RunSpec(run_id="rb", goal="g", project_dir=str(repo))
    Scheduler(
        worker=Failing(), registry=_registry(), state_root=tmp_path / "state",
        config=SchedulerConfig(isolate=True, verify=False, max_attempts=2),
    ).run(spec, [_task()])
    assert len(trees) == 2
    assert not (repo / "junk.py").exists(), "a failed attempt never reached the main tree"


def test_rollback_emits_an_event(tmp_path):
    repo = _repo(tmp_path)

    class Failing(MockWorker):
        def run(self, brief):
            self.briefs.append(brief)
            (Path(brief.workdir) / "x.py").write_text("x\n", encoding="utf-8")
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.FAIL, summary="x",
                classification=FailureKind.CODE_BUG, usage=Usage(cost_usd=0.1),
                model=brief.model, effort=brief.effort,
            )

    spec = RunSpec(run_id="rb2", goal="g", project_dir=str(repo))
    state = Scheduler(
        worker=Failing(), registry=_registry(), state_root=tmp_path / "state",
        config=SchedulerConfig(isolate=True, verify=False, max_attempts=2),
    ).run(spec, [_task()])
    assert any(e["kind"] == "rolled_back" for e in state.events)


def test_rollback_does_nothing_without_isolation(tmp_path):
    """In a shared tree the same operation would revert files the runtime cannot
    prove belong to this task alone."""
    repo = _repo(tmp_path)
    worker = MockWorker(scripted={"backend-python-a": [
        TaskResult(task_id="backend-python-a", verdict=Verdict.FAIL, summary="x",
                   classification=FailureKind.CODE_BUG, usage=Usage(cost_usd=0.1))
    ] * 6})
    spec = RunSpec(run_id="rb3", goal="g", project_dir=str(repo))
    state = Scheduler(worker=worker, registry=_registry(),
                      config=SchedulerConfig(verify=False)).run(spec, [_task()])
    assert not any(e["kind"] == "rolled_back" for e in state.events)


@pytest.mark.parametrize("flag", [True, False])
def test_rollback_respects_its_switch(tmp_path, flag):
    repo = _repo(tmp_path)

    class Failing(MockWorker):
        def run(self, brief):
            self.briefs.append(brief)
            (Path(brief.workdir) / "x.py").write_text("x\n", encoding="utf-8")
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.FAIL, summary="x",
                classification=FailureKind.CODE_BUG, usage=Usage(cost_usd=0.1),
                model=brief.model, effort=brief.effort,
            )

    spec = RunSpec(run_id=f"rb4-{flag}", goal="g", project_dir=str(repo))
    state = Scheduler(
        worker=Failing(), registry=_registry(), state_root=tmp_path / "state",
        config=SchedulerConfig(isolate=True, verify=False, max_attempts=2, rollback=flag),
    ).run(spec, [_task()])
    assert any(e["kind"] == "rolled_back" for e in state.events) is flag
