"""Worktree isolation, against real git.

No mocks here on purpose: the whole value of this module is what git actually
does with worktrees, three-way applies and conflicts, and a mocked git would be
testing my beliefs about it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vise.runtime.isolation import (
    IsolationUnavailable,
    WorktreePool,
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=repo, check=True, capture_output=True)
    (repo / "shared.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def pool(tmp_path):
    p = WorktreePool.create(_repo(tmp_path), tmp_path / "iso", "run-1")
    yield p
    p.cleanup()


# --- creation -------------------------------------------------------------


def test_a_non_repository_refuses_at_construction(tmp_path):
    """A caller that asked for isolation should find out before a run starts."""
    with pytest.raises(IsolationUnavailable):
        WorktreePool.create(tmp_path, tmp_path / "iso", "r")


def test_a_repository_with_no_commit_refuses(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    with pytest.raises(IsolationUnavailable):
        WorktreePool.create(repo, tmp_path / "iso", "r")


def test_each_task_gets_its_own_tree(pool):
    a = pool.acquire("task-a")
    b = pool.acquire("task-b")
    assert a != b
    assert (a / "shared.txt").read_text() == (b / "shared.txt").read_text()


def test_acquiring_twice_returns_the_same_tree(pool):
    assert pool.acquire("t") == pool.acquire("t")


def test_a_worktree_branches_from_the_main_head_not_from_a_peer(pool):
    """Chaining tasks would make the order they happened to start in part of
    the result."""
    a = pool.acquire("task-a")
    (a / "only-in-a.txt").write_text("x\n", encoding="utf-8")
    b = pool.acquire("task-b")
    assert not (b / "only-in-a.txt").exists()


def test_a_write_in_one_tree_is_invisible_to_the_other(pool):
    a = pool.acquire("a")
    pool.acquire("b")
    (a / "new.py").write_text("x\n", encoding="utf-8")
    assert pool.changed_paths("a") == ("new.py",)
    assert pool.changed_paths("b") == ()


# --- integration ----------------------------------------------------------


def test_a_clean_change_lands_in_the_main_tree(pool):
    wt = pool.acquire("t")
    (wt / "src").mkdir()
    (wt / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
    result = pool.integrate("t")
    assert result.applied
    assert result.changed_paths == ("src/new.py",)
    assert (pool.project_dir / "src" / "new.py").read_text() == "x = 1\n"


def test_a_modification_lands_too(pool):
    wt = pool.acquire("t")
    (wt / "shared.txt").write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
    assert pool.integrate("t").applied
    assert "CHANGED" in (pool.project_dir / "shared.txt").read_text()


def test_a_deletion_lands_too(pool):
    wt = pool.acquire("t")
    (wt / "shared.txt").unlink()
    assert pool.integrate("t").applied
    assert not (pool.project_dir / "shared.txt").exists()


def test_two_disjoint_tasks_both_land(pool):
    for name, path in (("a", "src/a.py"), ("b", "web/b.js")):
        wt = pool.acquire(name)
        target = wt / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"// {name}\n", encoding="utf-8")
        assert pool.integrate(name).applied, name
    assert (pool.project_dir / "src" / "a.py").exists()
    assert (pool.project_dir / "web" / "b.js").exists()


def test_a_task_that_changed_nothing_integrates_trivially(pool):
    pool.acquire("t")
    result = pool.integrate("t")
    assert result.applied and result.changed_paths == ()


def test_integrating_an_unknown_task_reports_rather_than_raises(pool):
    result = pool.integrate("never-acquired")
    assert not result.applied
    assert "no worktree" in result.reason


# --- conflicts ------------------------------------------------------------


def test_two_tasks_changing_the_same_lines_conflict_and_say_so(pool):
    for name, text in (("a", "line1\nFROM A\nline3\n"), ("b", "line1\nFROM B\nline3\n")):
        wt = pool.acquire(name)
        (wt / "shared.txt").write_text(text, encoding="utf-8")

    assert pool.integrate("a").applied
    second = pool.integrate("b")
    assert not second.applied
    assert "ownership declaration was wrong or the plan was" in second.reason


def test_a_refused_integration_leaves_the_main_tree_as_it_was(pool):
    """A half-applied patch is the one state nobody can reason about."""
    for name, text in (("a", "line1\nFROM A\nline3\n"), ("b", "line1\nFROM B\nline3\n")):
        wt = pool.acquire(name)
        (wt / "shared.txt").write_text(text, encoding="utf-8")
    pool.integrate("a")
    before = (pool.project_dir / "shared.txt").read_text()
    pool.integrate("b")
    after = (pool.project_dir / "shared.txt").read_text()
    assert after == before
    assert "<<<<<<<" not in after, "no conflict markers are left behind"


def test_the_conflicting_task_is_named_in_the_result(pool):
    for name, text in (("a", "x\ny\nz\n"), ("b", "1\n2\n3\n")):
        wt = pool.acquire(name)
        (wt / "shared.txt").write_text(text, encoding="utf-8")
    pool.integrate("a")
    result = pool.integrate("b")
    assert result.task_id == "b"
    assert not result


# --- cleanup --------------------------------------------------------------


def test_release_removes_the_tree_and_its_branch(pool):
    wt = pool.acquire("t")
    branch = pool.branch_for("t")
    pool.release("t")
    assert not wt.exists()
    listed = subprocess.run(["git", "branch", "--list", branch],
                            cwd=pool.project_dir, capture_output=True, text=True)
    assert not listed.stdout.strip()


def test_release_is_safe_to_call_twice(pool):
    pool.acquire("t")
    pool.release("t")
    pool.release("t")


def test_cleanup_removes_everything(pool):
    paths = [pool.acquire(f"t{i}") for i in range(3)]
    pool.cleanup()
    assert not any(p.exists() for p in paths)
    assert pool.worktrees == {}


def test_a_task_id_with_path_characters_is_slugged(pool):
    wt = pool.acquire("auth::verify/../escape")
    assert pool.root.resolve() in wt.resolve().parents


# --- the scheduler under isolation ---------------------------------------


def _registry():
    from vise.runtime.registry import AgentRegistry, AgentSpec

    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d", model="sonnet",
                  capabilities=("backend", "python")),
        AgentSpec(id="frontend", role="frontend", description="d", model="sonnet",
                  capabilities=("frontend",)),
        AgentSpec(id="verifier", role="verify", description="d", model="sonnet",
                  effort="medium", writes=False, capabilities=("verify",)),
    ):
        reg.agents[spec.id] = spec
    return reg


def _writing_worker(files: dict[str, str], verdicts=None):
    from vise.runtime.contracts import TaskResult, Usage, Verdict
    from vise.runtime.worker import MockWorker

    verdicts = dict(verdicts or {})

    class Writer(MockWorker):
        def run(self, brief):
            self.briefs.append(brief)
            if brief.role == "verify":
                from vise.runtime.contracts import Artifact

                base = brief.task_id.split("::")[0]
                return TaskResult(
                    task_id=brief.task_id, verdict=Verdict.PASS,
                    artifacts=(Artifact(brief.run_id, brief.task_id, "verification",
                                        {"verdict": "pass"}),),
                    usage=Usage(cost_usd=0.1),
                ) if base else TaskResult(task_id=brief.task_id, verdict=Verdict.PASS)
            rel = files.get(brief.task_id)
            if rel:
                # `brief` does not carry the tree it runs in, so write relative
                # to the process cwd the scheduler set... which it does not set.
                # The path comes from the context instead.
                target = Path(_tree_of(brief)) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"# {brief.task_id}\n", encoding="utf-8")
            return TaskResult(
                task_id=brief.task_id,
                verdict=verdicts.get(brief.task_id, Verdict.PASS),
                summary="ok", evidence="$ t\nok", checks="$ c\nok",
                usage=Usage(cost_usd=0.1), model=brief.model, effort=brief.effort,
            )

    return Writer()


_TREES: dict[str, str] = {}


def _tree_of(brief) -> str:
    return _TREES[brief.task_id]


class _TreeRecordingScheduler:
    """Captures where the scheduler decided each task should run."""

    @staticmethod
    def patch(monkeypatch):
        from vise.runtime.scheduler import Scheduler

        original = Scheduler._tree_for

        def spy(self, state, task, brief):
            tree = original(self, state, task, brief)
            _TREES[task.id] = tree
            return tree

        monkeypatch.setattr(Scheduler, "_tree_for", spy)


def test_each_writing_task_runs_in_its_own_tree_and_lands_after_verifying(
    tmp_path, monkeypatch
):
    from vise.engines.graph_engine import Task
    from vise.runtime.contracts import RunBudget, RunSpec
    from vise.runtime.scheduler import Scheduler, SchedulerConfig

    _TREES.clear()
    _TreeRecordingScheduler.patch(monkeypatch)
    repo = _repo(tmp_path)
    worker = _writing_worker({
        "backend-python-auth": "src/auth/token.py",
        "frontend-login": "web/src/login/form.tsx",
    })
    tasks = [
        Task(id="backend-python-auth", name="a", role="backend",
             ownership=["src/auth/**"], acceptance=["it works"]),
        Task(id="frontend-login", name="b", role="frontend",
             ownership=["web/src/login/**"], acceptance=["it works"]),
    ]
    spec = RunSpec(run_id="iso-1", goal="g", project_dir=str(repo),
                   budget=RunBudget(max_parallel=2))
    state = Scheduler(
        worker=worker, registry=_registry(), state_root=tmp_path / "state",
        config=SchedulerConfig(isolate=True),
    ).run(spec, tasks)

    assert state.succeeded(), {t: r.note for t, r in state.tasks.items()}
    # each ran somewhere that is not the main tree
    assert set(_TREES) == {"backend-python-auth", "frontend-login"}
    assert all(Path(t) != repo for t in _TREES.values())
    assert len(set(_TREES.values())) == 2
    # and both landed
    assert (repo / "src" / "auth" / "token.py").exists()
    assert (repo / "web" / "src" / "login" / "form.tsx").exists()
    assert any(e["kind"] == "integrated" for e in state.events)


def test_a_conflicting_integration_blocks_the_task_and_names_the_reason(
    tmp_path, monkeypatch
):
    from vise.engines.graph_engine import Task
    from vise.runtime.contracts import RunBudget, RunSpec, TaskState
    from vise.runtime.scheduler import Scheduler, SchedulerConfig

    _TREES.clear()
    _TreeRecordingScheduler.patch(monkeypatch)
    repo = _repo(tmp_path)

    from vise.runtime.contracts import TaskResult, Usage, Verdict
    from vise.runtime.worker import MockWorker

    class Clasher(MockWorker):
        """Both tasks rewrite the same seeded file, differently."""

        def run(self, brief):
            self.briefs.append(brief)
            target = Path(_TREES[brief.task_id]) / "shared.txt"
            target.write_text(f"line1\nFROM {brief.task_id}\nline3\n", encoding="utf-8")
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS, summary="ok",
                evidence="$ t\nok", checks="$ c\nok", usage=Usage(cost_usd=0.1),
                model=brief.model, effort=brief.effort,
            )

    tasks = [
        Task(id="backend-python-a", name="a", role="backend", ownership=["shared.txt"]),
        Task(id="backend-python-b", name="b", role="backend", ownership=["shared.txt"]),
    ]
    spec = RunSpec(run_id="iso-2", goal="g", project_dir=str(repo),
                   budget=RunBudget(max_parallel=1))
    state = Scheduler(
        worker=Clasher(), registry=_registry(), state_root=tmp_path / "state",
        config=SchedulerConfig(isolate=True, verify=False, max_attempts=1),
    ).run(spec, tasks)

    states = {t: r.state for t, r in state.tasks.items()}
    assert TaskState.SUCCEEDED in states.values(), "the first task lands"
    blocked = [t for t, st in states.items() if st is not TaskState.SUCCEEDED]
    assert blocked, "the second cannot"
    assert "integrate" in state.tasks[blocked[0]].note
    assert "<<<<<<<" not in (repo / "shared.txt").read_text()


def test_a_non_repository_degrades_to_the_shared_tree_rather_than_refusing(tmp_path):
    from vise.engines.graph_engine import Task
    from vise.runtime.contracts import RunSpec
    from vise.runtime.scheduler import Scheduler, SchedulerConfig
    from vise.runtime.worker import MockWorker

    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    spec = RunSpec(run_id="iso-3", goal="g", project_dir=str(plain))
    tasks = [Task(id="backend-python-a", name="a", role="backend", ownership=["src/**"])]
    state = Scheduler(
        worker=MockWorker(), registry=_registry(),
        config=SchedulerConfig(isolate=True),
    ).run(spec, tasks)
    assert state.succeeded()
    assert any(e["kind"] == "isolation_unavailable" for e in state.events)


def test_isolation_is_off_by_default(tmp_path):
    from vise.engines.graph_engine import Task
    from vise.runtime.contracts import RunSpec
    from vise.runtime.scheduler import Scheduler
    from vise.runtime.worker import MockWorker

    repo = _repo(tmp_path)
    spec = RunSpec(run_id="iso-4", goal="g", project_dir=str(repo))
    tasks = [Task(id="backend-python-a", name="a", role="backend", ownership=["src/**"])]
    state = Scheduler(worker=MockWorker(), registry=_registry()).run(spec, tasks)
    assert not any(e["kind"].startswith("isolation") for e in state.events)
