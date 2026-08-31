"""One real run of the runtime, against a real repository, in the suite.

Tier T7. Every other test drives one component with the rest mocked, and that is
what let twelve bugs through in a day of real use: each was in a seam. This one
runs the actual scheduler against an actual git repository with the actual
gates — no conftest patches of the spec gate, no mocked git, no stubbed honesty —
and asserts what a person watching the run would check.

Deliberately not a smoke test. Each assertion below is a property some past bug
broke: the ledger reporting money it did not spend, a bare-directory ownership
claim refusing its own writes, a second edit to a dirty file reading as no edit,
worktrees left behind, the run state file missing while the first task runs.

It uses a mock *worker* — the model is the one thing that must not be in the
loop, because a test that spends money is a test nobody runs. Everything below
the worker is real.
"""
from __future__ import annotations

import json
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

SPEC = """## ADDED Requirements

### Requirement: The thing exists
The system SHALL provide a thing.

#### Scenario: asking for the thing
- **WHEN** someone asks
- **THEN** it answers
"""


@pytest.fixture
def project(tmp_path):
    """A repository shaped like one someone actually works in."""
    repo = tmp_path / "project"
    (repo / "src" / "alpha").mkdir(parents=True)
    (repo / "src" / "beta").mkdir(parents=True)

    def run(*args):
        subprocess.run(args, cwd=repo, capture_output=True, check=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")
    (repo / "src" / "alpha" / "mod.py").write_text(
        "\n".join(f"line {i}" for i in range(40)) + "\n", encoding="utf-8")
    (repo / "src" / "beta" / "mod.py").write_text("b = 1\n", encoding="utf-8")

    change = repo / "openspec" / "changes" / "e2e"
    (change / "specs" / "thing").mkdir(parents=True)
    (change / "proposal.md").write_text(
        "# E2E\n\n## Why\nTo exercise the runtime.\n\n## What Changes\n- A thing.\n",
        encoding="utf-8")
    (change / "specs" / "thing" / "spec.md").write_text(SPEC, encoding="utf-8")
    (change / "tasks.md").write_text("- [ ] 1.1 do it\n", encoding="utf-8")

    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return repo


class _Worker:
    """Writes inside its own ownership and reports it, like a real one."""

    def __init__(self, cost: float = 1.0):
        self.cost = cost
        self.dispatched: list[str] = []

    def run(self, brief):
        from pathlib import Path

        self.dispatched.append(brief.task_id)
        work = Path(brief.workdir or brief.project_dir)
        area = "alpha" if brief.task_id == "a" else "beta"
        target = work / "src" / area / "mod.py"
        target.write_text(target.read_text() + f"# {brief.task_id} was here\n",
                          encoding="utf-8")
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.PASS, summary="done",
            evidence="$ pytest\n1 passed",
            checks="$ ruff\nAll checks passed!",
            changed_paths=(f"src/{area}/mod.py",),
            usage=Usage(cost_usd=self.cost, tokens_in=10, tokens_out=20),
        )


def _tasks():
    return [
        # Bare directory claims, not globs — the shape that used to be admitted
        # and then have its every write refused.
        Task(id="a", name="A python", role="backend", ownership=["src/alpha"]),
        Task(id="b", name="B python", role="backend", ownership=["src/beta"]),
    ]


def _run(project, tmp_path, **config):
    worker = _Worker()
    state = Scheduler(
        worker=worker, registry=AgentRegistry.bundled(), state_root=tmp_path,
        config=SchedulerConfig(**config),
    ).run(
        RunSpec(run_id="e2e", goal="build the thing", project_dir=str(project),
                budget=RunBudget(max_parallel=2)),
        _tasks(),
    )
    return worker, state


@pytest.mark.parametrize("isolate", [False, True], ids=["shared-tree", "isolate"])
def test_a_real_run_succeeds_with_the_real_gates(project, tmp_path, isolate):
    worker, state = _run(project, tmp_path, isolate=isolate)

    assert state.succeeded(), {
        t: (r.state.value, r.note) for t, r in state.tasks.items()
    }
    assert sorted(worker.dispatched) == ["a", "b"]
    assert all(r.attempt_count == 1 for r in state.tasks.values()), (
        "a task needed a second attempt, which means a gate refused honest work"
    )


@pytest.mark.parametrize("isolate", [False, True], ids=["shared-tree", "isolate"])
def test_the_work_reaches_the_working_tree(project, tmp_path, isolate):
    _run(project, tmp_path, isolate=isolate)

    assert "# a was here" in (project / "src" / "alpha" / "mod.py").read_text()
    assert "# b was here" in (project / "src" / "beta" / "mod.py").read_text()


@pytest.mark.parametrize("isolate", [False, True], ids=["shared-tree", "isolate"])
def test_the_ledger_reports_exactly_what_was_spent(project, tmp_path, isolate):
    worker, state = _run(project, tmp_path, isolate=isolate)

    assert state.ledger.spent.cost_usd == pytest.approx(worker.cost * 2)
    assert state.ledger.reserved == {}


@pytest.mark.parametrize("isolate", [False, True], ids=["shared-tree", "isolate"])
def test_the_run_leaves_the_repository_tidy(project, tmp_path, isolate):
    _run(project, tmp_path, isolate=isolate)

    worktrees = subprocess.run(
        ("git", "worktree", "list", "--porcelain"),
        cwd=project, capture_output=True, text=True).stdout
    assert worktrees.count("worktree ") == 1, f"worktrees survived:\n{worktrees}"

    branches = subprocess.run(
        ("git", "branch", "--format=%(refname:short)"),
        cwd=project, capture_output=True, text=True).stdout
    assert "vise/" not in branches, f"branches survived: {branches!r}"


def test_the_users_uncommitted_edit_survives_a_run(project, tmp_path):
    """The state everyone is actually in when they start a run."""
    target = project / "src" / "alpha" / "mod.py"
    lines = target.read_text().split("\n")
    lines[0] = "# the user was editing this"
    target.write_text("\n".join(lines), encoding="utf-8")

    _run(project, tmp_path, isolate=True)

    landed = target.read_text()
    assert "# the user was editing this" in landed, "the user's edit was lost"
    assert "# a was here" in landed


def test_the_run_state_file_is_readable_while_the_run_is_going(project, tmp_path):
    """`vise runtime status` reads this file from another process."""
    seen: list[object] = []

    class Watcher(_Worker):
        def run(self, brief):
            mid = RunState.load(tmp_path, "e2e")
            seen.append(None if mid is None else mid.tasks[brief.task_id].state)
            return super().run(brief)

    Scheduler(
        worker=Watcher(), registry=AgentRegistry.bundled(), state_root=tmp_path,
    ).run(
        RunSpec(run_id="e2e", goal="g", project_dir=str(project),
                budget=RunBudget(max_parallel=1)),
        _tasks(),
    )

    assert seen and all(s is TaskState.RUNNING for s in seen), (
        f"a reader mid-run saw {seen}, not RUNNING"
    )


def test_the_spec_gate_blocks_a_project_with_no_change(project, tmp_path, monkeypatch):
    """The real gate, unpatched — the conftest fixture is bypassed here."""
    from vise.runtime import scheduler as scheduler_module
    from vise.runtime.spec_gate import check as real_check

    monkeypatch.setattr(scheduler_module, "spec_gate_check", real_check)
    monkeypatch.delenv("VISE_NODE_GATE_OVERRIDE", raising=False)
    subprocess.run(("git", "rm", "-r", "-q", "openspec"), cwd=project,
                   capture_output=True, check=True)

    _worker, state = _run(project, tmp_path)

    assert not state.succeeded()
    assert state.ledger.spent.cost_usd == 0.0, "a blocked run spent money"
    assert all(r.state is TaskState.BLOCKED for r in state.tasks.values())


def test_the_persisted_state_survives_a_round_trip(project, tmp_path):
    _worker, state = _run(project, tmp_path)

    reloaded = RunState.load(tmp_path, "e2e")

    assert reloaded is not None
    assert set(reloaded.tasks) == set(state.tasks)
    assert reloaded.ledger.spent.cost_usd == pytest.approx(
        state.ledger.spent.cost_usd)
    raw = json.loads((tmp_path / "runs" / "e2e" / "state.json").read_text())
    assert raw["spec"]["run_id"] == "e2e"
