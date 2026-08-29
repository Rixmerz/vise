"""Four bugs that only an end-to-end run found, and the tests that now hold them.

Every unit test in the suite wrote into a directory that already existed, ran one
writer at a time, or never retried a task that had already produced its output.
Each of those is the normal case; none of them is the interesting one. These are
the interesting ones.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import (
    RunBudget,
    RunSpec,
    TaskBrief,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.honesty import check_result, tree_hash
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler
from vise.runtime.worker import MockWorker


def _repo(tmp_path: Path) -> Path:
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@e.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True,
                   capture_output=True)
    return tmp_path


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d", model="sonnet",
                  capabilities=("backend", "python")),
        AgentSpec(id="frontend", role="frontend", description="d", model="sonnet",
                  capabilities=("frontend",)),
    ):
        reg.agents[spec.id] = spec
    return reg


# --- bug 1: --porcelain collapses a new untracked directory ---------------


def test_a_file_in_a_brand_new_directory_is_reported_as_the_file(tmp_path):
    """`git status --porcelain` answers `src/` for the first file under a new
    directory. Read that way, a task owning `src/auth/**` is refused for writing
    outside its claim the moment it creates its own first file."""
    from vise.runtime.adapters.claude_code import _tracked_paths

    repo = _repo(tmp_path)
    (repo / "src" / "auth").mkdir(parents=True)
    (repo / "src" / "auth" / "token.py").write_text("x\n", encoding="utf-8")
    paths = _tracked_paths(repo)
    assert paths is not None
    assert "src/auth/token.py" in paths
    assert "src/" not in paths


def test_the_adapter_attributes_a_new_directorys_file_to_its_owner(tmp_path):
    from vise.runtime.adapters.claude_code import ClaudeCodeWorker

    repo = _repo(tmp_path)

    def runner(argv, **_):
        target = repo / "src" / "auth" / "token.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0,
            '{"type":"result","is_error":false,"result":"```vise-result\\n'
            '{\\"verdict\\":\\"pass\\",\\"summary\\":\\"ok\\",\\"evidence\\":\\"e\\",'
            '\\"checks\\":\\"c\\"}\\n```"}', "",
        )

    brief = TaskBrief(run_id="r", task_id="t", name="t", role="backend",
                      ownership=("src/auth/**",))
    result = ClaudeCodeWorker(project_dir=repo, runner=runner).run(brief)
    assert result.changed_paths == ("src/auth/token.py",)
    outcome = check_result(brief, result, baseline_tree="a", current_tree="b")
    assert outcome.accepted, outcome.refusals


# --- bug 2: one shared tree cannot attribute a file to a writer -----------


def test_a_peers_file_is_not_charged_to_this_task():
    brief = TaskBrief(run_id="r", task_id="backend", name="b", role="backend",
                      ownership=("src/auth/**",))
    result = TaskResult(
        task_id="backend", verdict=Verdict.PASS, summary="ok",
        evidence="e", checks="c",
        changed_paths=("src/auth/token.py", "web/src/login/form.tsx"),
    )
    refused = check_result(brief, result, baseline_tree="a", current_tree="b")
    assert not refused.accepted, "without the peer's claim this is an escape"

    excused = check_result(
        brief, result, baseline_tree="a", current_tree="b",
        foreign_ownership=("web/src/login/**",),
    )
    assert excused.accepted


def test_a_conflicting_peers_claim_never_excuses_anything():
    """Admission serialises conflicting tasks, so a conflicting peer's file
    appearing mid-run is not a peer being busy — it is something wrong."""
    brief = TaskBrief(run_id="r", task_id="t", name="t", role="backend",
                      ownership=("src/auth/**",))
    result = TaskResult(task_id="t", verdict=Verdict.PASS, summary="ok",
                        evidence="e", checks="c", changed_paths=("src/db/x.py",))
    outcome = check_result(brief, result, baseline_tree="a", current_tree="b",
                           foreign_ownership=())
    assert not outcome.accepted


def test_two_peers_dispatched_in_one_pass_do_not_refuse_each_other(tmp_path):
    """The bug the end-to-end run hit: foreign ownership read from an in-flight
    snapshot means the first task started misses the second, which does not
    exist yet."""
    repo = _repo(tmp_path)
    barrier = threading.Barrier(2, timeout=10)

    class Writer(MockWorker):
        def run(self, brief):
            area = "src/auth" if brief.task_id.startswith("backend") else "web/src/login"
            target = repo / area / f"{brief.task_id}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n", encoding="utf-8")
            barrier.wait()          # both are live at the same moment
            time.sleep(0.02)        # and both see the other's file
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS, summary="ok",
                evidence="$ t\nok", checks="$ c\nok", usage=Usage(cost_usd=0.01),
                model=brief.model, effort=brief.effort,
            )

    tasks = [
        Task(id="backend-python-auth", name="a", role="backend", ownership=["src/auth/**"]),
        Task(id="frontend-login", name="b", role="frontend", ownership=["web/src/login/**"]),
    ]
    spec = RunSpec(run_id="r", goal="g", project_dir=str(repo),
                   budget=RunBudget(max_parallel=2))
    state = Scheduler(worker=Writer(), registry=_registry()).run(spec, tasks)
    assert state.succeeded(), {t: r.note for t, r in state.tasks.items()}


# --- bug 3: a retry is judged against where the task began ---------------


def test_a_retry_that_reproduces_its_own_output_is_not_refused(tmp_path):
    """Attempt 1 writes the file and is refused for something else. Attempt 2
    writes the same file, so nothing changed *since attempt 2 started* — and
    comparing against that would refuse it for doing what it was asked again."""
    repo = _repo(tmp_path)
    attempts: list[int] = []

    class Idempotent(MockWorker):
        def run(self, brief):
            attempts.append(1)
            target = repo / "src" / "auth" / "token.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x = 1\n", encoding="utf-8")
            verdict = Verdict.FAIL if len(attempts) == 1 else Verdict.PASS
            return TaskResult(
                task_id=brief.task_id, verdict=verdict, summary="s",
                evidence="$ t\nok", checks="$ c\nok",
                changed_paths=("src/auth/token.py",),
                usage=Usage(cost_usd=0.01), model=brief.model, effort=brief.effort,
            )

    tasks = [Task(id="backend-python-auth", name="a", role="backend",
                  ownership=["src/auth/**"])]
    spec = RunSpec(run_id="r", goal="g", project_dir=str(repo))
    state = Scheduler(worker=Idempotent(), registry=_registry()).run(spec, tasks)
    assert len(attempts) == 2
    assert state.tasks["backend-python-auth"].state is TaskState.SUCCEEDED, \
        state.tasks["backend-python-auth"].note


def test_a_supplied_baseline_wins_over_the_one_execute_would_take(tmp_path):
    from vise.runtime.worker import execute

    repo = _repo(tmp_path)
    (repo / "already.py").write_text("x\n", encoding="utf-8")
    stale = tree_hash(repo)
    (repo / "more.py").write_text("y\n", encoding="utf-8")

    brief = TaskBrief(run_id="r", task_id="t", name="t", role="docs",
                      ownership=("**",))
    worker = MockWorker(scripted={"t": [
        TaskResult(task_id="t", verdict=Verdict.PASS, summary="ok",
                   evidence="e", checks="c")
    ]})
    # The worker writes nothing, but the supplied baseline predates `more.py`,
    # so the task's own history counts as movement.
    _, outcome = execute(brief, worker, project_dir=repo, baseline_tree=stale)
    assert outcome.accepted


# --- bug 4: a stopped run must not resurrect a task -----------------------


def test_is_done_implies_nothing_is_still_live(tmp_path):
    """Covered under load in test_runtime_stress; pinned here as the invariant
    itself rather than as a property of a forty-task graph."""
    repo = _repo(tmp_path)

    class Failing(MockWorker):
        def run(self, brief):
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.INCONCLUSIVE,
                summary="never evaluated", usage=Usage(cost_usd=0.01),
                model=brief.model, effort=brief.effort,
            )

    tasks = [
        Task(id=f"backend-python-{i}", name=f"t{i}", role="backend",
             ownership=[f"src/a{i}/**"])
        for i in range(6)
    ]
    spec = RunSpec(run_id="r", goal="g", project_dir=str(repo),
                   budget=RunBudget(max_parallel=3))
    state = Scheduler(worker=Failing(), registry=_registry()).run(spec, tasks)
    assert state.is_done()
    live = [r.task_id for r in state.tasks.values()
            if r.state in (TaskState.PENDING, TaskState.READY, TaskState.RUNNING)]
    assert not live


@pytest.mark.parametrize("run", range(3))
def test_the_parallel_attribution_fix_is_not_timing_dependent(tmp_path, run):
    test_two_peers_dispatched_in_one_pass_do_not_refuse_each_other(tmp_path)
