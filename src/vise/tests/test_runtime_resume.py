"""A stopped run can be picked up, and the money it spent still counts.

`RunState.MAX_EVENTS` carries the comment "the state file is read on every
resume". There was no resume: `RunState.load` existed and its only two callers
were the read-only `status` and `explain` commands, while the loop stops for a
person at nine construction points. Nine recorded runs, three of them parked,
none continuable.

The load-bearing test here is the ledger one. A resumed run that forgot its
spend would turn `--max-cost` into a per-attempt limit, and a caller could
spend without bound by resuming — which is the failure this runtime's whole
budget layer exists to prevent.
"""
from __future__ import annotations

import subprocess

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import (
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.registry import AgentRegistry
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.state import RunState


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "project"
    (repo / "src" / "alpha").mkdir(parents=True)
    (repo / "src" / "beta").mkdir(parents=True)

    def run(*args):
        subprocess.run(args, cwd=repo, capture_output=True, check=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (repo / "src" / "alpha" / "mod.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "src" / "beta" / "mod.py").write_text("b = 1\n", encoding="utf-8")

    # The runtime's spec gate refuses to dispatch writing work without a
    # well-formed change, so a repository that can be run against has one.
    change = repo / "openspec" / "changes" / "resume"
    (change / "specs" / "thing").mkdir(parents=True)
    (change / "proposal.md").write_text(
        "# Resume\n\n## Why\nTo exercise resume.\n\n## What Changes\n- A thing.\n",
        encoding="utf-8")
    (change / "specs" / "thing" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: The thing exists\n"
        "The system SHALL provide a thing.\n\n"
        "#### Scenario: asking for the thing\n- **WHEN** someone asks\n"
        "- **THEN** it answers\n",
        encoding="utf-8")
    (change / "tasks.md").write_text("- [ ] 1.1 do it\n", encoding="utf-8")

    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return repo


class _Worker:
    def __init__(self, cost: float = 1.0):
        self.cost = cost
        self.dispatched: list[str] = []

    def run(self, brief):
        from pathlib import Path

        self.dispatched.append(brief.task_id)
        area = "alpha" if brief.task_id == "a" else "beta"
        target = Path(brief.workdir or brief.project_dir) / "src" / area / "mod.py"
        target.write_text(target.read_text() + f"# {brief.task_id}\n", encoding="utf-8")
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.PASS, summary="done",
            evidence="$ pytest\n1 passed", checks="$ ruff\nAll checks passed!",
            changed_paths=(f"src/{area}/mod.py",),
            usage=Usage(cost_usd=self.cost, tokens_in=10, tokens_out=20),
        )


def _tasks():
    # "python" in the name is load-bearing: twelve agents take the `backend`
    # role, so the capability hint is what makes the task routable at all.
    return [
        Task(id="a", name="A python", role="backend", ownership=["src/alpha"]),
        Task(id="b", name="B python", role="backend", ownership=["src/beta"]),
    ]


def _spec(project, **fields):
    return RunSpec(run_id="r", goal="g", project_dir=str(project),
                   budget=RunBudget(max_parallel=2), **fields)


def _parked(project, tmp_path, **spec_fields) -> tuple[RunState, Scheduler]:
    """A run where `a` succeeded and `b` is parked behind a human gate."""
    worker = _Worker()
    scheduler = Scheduler(worker=worker, registry=AgentRegistry.bundled(),
                          state_root=tmp_path, config=SchedulerConfig())
    state = RunState.for_tasks(_spec(project, **spec_fields), {"a", "b"})
    record = state.record("a")
    record.state = TaskState.SUCCEEDED
    record.result = TaskResult(task_id="a", verdict=Verdict.PASS, summary="already done")
    state.ledger.spend("a", Usage(cost_usd=2.50, tokens_in=1, tokens_out=1))
    state.stop_for_human("a person must decide")
    return state, scheduler


# --- what carries over -----------------------------------------------------


def test_a_succeeded_task_is_not_run_again(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)

    resumed = scheduler.resume(state, _tasks())

    assert scheduler.worker.dispatched == ["b"], "paid twice for the same answer"
    assert resumed.tasks["a"].state is TaskState.SUCCEEDED
    assert resumed.tasks["a"].result.summary == "already done"


def test_the_human_gate_is_cleared_by_the_act_of_resuming(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    assert state.human_gate

    resumed = scheduler.resume(state, _tasks())

    assert resumed.human_gate == ""
    assert resumed.succeeded()


def test_the_spend_carries_over(project, tmp_path):
    """Otherwise --max-cost becomes a per-attempt limit a loop can bypass."""
    state, scheduler = _parked(project, tmp_path)

    resumed = scheduler.resume(state, _tasks())

    assert resumed.ledger.spent.cost_usd == pytest.approx(2.50 + 1.0), (
        "the resumed run forgot what the first attempt cost"
    )


def test_a_stale_reservation_is_released(project, tmp_path):
    """A reservation held for a task that never reported would hold budget for
    work that is about to be attempted again."""
    state, scheduler = _parked(project, tmp_path)
    state.ledger.reserve("b", 99.0)

    resumed = scheduler.resume(state, _tasks())

    assert "b" not in resumed.ledger.reserved
    assert resumed.ledger.reserved == {}


def test_the_replan_count_is_not_a_fresh_budget(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    state.replans = 2

    assert scheduler.resume(state, _tasks()).replans == 2


def test_resuming_says_what_it_did(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)

    resumed = scheduler.resume(state, _tasks())

    [event] = [e for e in resumed.events if e["kind"] == "resumed"]
    assert event["retrying"] == 1
    assert event["kept"] == 1
    assert event["spent_usd"] == pytest.approx(2.50)


def test_the_earlier_events_survive(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    state.emit("something_earlier", detail="x")

    kinds = [e["kind"] for e in scheduler.resume(state, _tasks()).events]

    assert "something_earlier" in kinds
    assert kinds.index("something_earlier") < kinds.index("resumed")


# --- what resets -----------------------------------------------------------


def test_a_blocked_task_is_retried(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    state.tasks["b"].state = TaskState.BLOCKED
    state.tasks["b"].note = "blocked on the gate"

    resumed = scheduler.resume(state, _tasks())

    assert scheduler.worker.dispatched == ["b"]
    assert resumed.tasks["b"].state is TaskState.SUCCEEDED
    assert resumed.tasks["b"].note == "" or "blocked on the gate" not in resumed.tasks["b"].note


def test_a_cancelled_run_can_be_resumed(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    state.human_gate = ""
    state.cancelled = True
    state.cancel_reason = "caller"

    resumed = scheduler.resume(state, _tasks())

    assert resumed.cancelled is False
    assert resumed.cancel_reason == ""
    assert resumed.succeeded()


def test_a_task_added_since_the_run_stopped_gets_a_record(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    extra = Task(id="c", name="C python", role="backend", ownership=["src/gamma"])

    resumed = scheduler.resume(state, [*_tasks(), extra])

    assert "c" in resumed.tasks
    assert "c" in scheduler.worker.dispatched


def test_a_finished_run_resumes_to_nothing(project, tmp_path):
    state, scheduler = _parked(project, tmp_path)
    state.tasks["b"].state = TaskState.SUCCEEDED
    state.human_gate = ""

    resumed = scheduler.resume(state, _tasks())

    assert scheduler.worker.dispatched == [], "a finished run was re-run"
    assert resumed.succeeded()


# --- the CLI ---------------------------------------------------------------


GRAPH = """\
metadata:
  name: "Resume Test"
nodes:
  - id: "build"
    name: "Build"
    is_start: true
    node_type: "dag"
    is_end: true
    tasks:
      - id: "a"
        name: "A"
        role: "backend"
        ownership: ["src/alpha"]
      - id: "b"
        name: "B"
        role: "backend"
        ownership: ["src/beta"]
edges: []
"""


def test_the_cli_reports_what_it_would_retry_without_dispatching(project, tmp_path, capsys):
    import argparse

    from vise.cli.runtime_cmd import _cmd_resume

    root = tmp_path / "state"
    state, _ = _parked(project, tmp_path)
    state.save(root)

    rc = _cmd_resume(argparse.Namespace(
        run_id="r", graph=None, project_dir=None, permission_mode=None,
        isolate=False, no_verify=False, change=None, state_dir=str(root), yes=False,
    ))

    out = capsys.readouterr().out
    assert rc == 0
    assert "would retry 1: b" in out
    assert "clearing the human gate" in out
    assert "already spent $2.50" in out
    assert "nothing dispatched" in out


def test_the_cli_refuses_a_run_whose_graph_it_cannot_find(project, tmp_path, capsys):
    import argparse

    from vise.cli.runtime_cmd import _cmd_resume

    root = tmp_path / "state"
    state, _ = _parked(project, tmp_path,
                       graph_name="no-such-graph", node_id="build")
    state.save(root)

    with pytest.raises(SystemExit) as exc:
        _cmd_resume(argparse.Namespace(
            run_id="r", graph=None, project_dir=None, permission_mode=None,
            isolate=False, no_verify=False, change=None, state_dir=str(root), yes=True,
        ))

    assert exc.value.code == 2
    assert "cannot find the graph" in capsys.readouterr().err


def test_the_cli_says_when_there_is_nothing_to_resume(project, tmp_path, capsys):
    import argparse

    from vise.cli.runtime_cmd import _cmd_resume

    root = tmp_path / "state"
    state, _ = _parked(project, tmp_path)
    state.tasks["b"].state = TaskState.SUCCEEDED
    state.save(root)

    rc = _cmd_resume(argparse.Namespace(
        run_id="r", graph=None, project_dir=None, permission_mode=None,
        isolate=False, no_verify=False, change=None, state_dir=str(root), yes=True,
    ))

    assert rc == 0
    assert "nothing to resume" in capsys.readouterr().out
