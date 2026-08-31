"""The gates a worker's own claim has to survive, plus the seam that runs them.

The tree-hash rule is the only mechanical honesty check in vise — every other
one asks another model, which means every other one can be talked out of its
finding. Its degraded behaviour therefore matters as much as its finding: a gate
that reports a violation when it could not compute its input is committing the
error it exists to catch.
"""
from __future__ import annotations

import subprocess

import pytest

from vise.runtime.contracts import TaskBrief, TaskResult, Usage, Verdict
from vise.runtime.honesty import check_result, tree_hash
from vise.runtime.worker import MockWorker, Worker, execute


def _brief(**kw) -> TaskBrief:
    base = dict(run_id="r", task_id="t1", name="n", role="backend")
    base.update(kw)
    return TaskBrief(**base)


def _pass(**kw) -> TaskResult:
    base = dict(task_id="t1", verdict=Verdict.PASS, summary="done",
                evidence="$ pytest\n1 passed", checks="$ ruff\nAll checks passed!")
    base.update(kw)
    return TaskResult(**base)


def _git_repo(tmp_path):
    def run(*args):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return tmp_path


# --- rule 1: evidence -----------------------------------------------------


def test_a_testing_pass_without_evidence_is_refused():
    outcome = check_result(_brief(role="test"), _pass(evidence=""))
    assert not outcome.accepted
    assert "without evidence" in outcome.refusals[0]


def test_a_testing_pass_with_whitespace_evidence_is_refused():
    assert not check_result(_brief(role="test"), _pass(evidence="   \n ")).accepted


def test_evidence_is_not_required_of_a_role_that_does_not_test():
    assert check_result(_brief(role="docs"), _pass(evidence="", checks="")).accepted


# --- rule 2: checks -------------------------------------------------------


def test_an_implementing_pass_without_checks_is_refused():
    outcome = check_result(_brief(role="backend"), _pass(checks=""))
    assert not outcome.accepted
    assert "without checks" in outcome.refusals[0]


# --- rule 3: the tree actually moved --------------------------------------


def test_a_pass_with_an_unchanged_tree_is_refused():
    outcome = check_result(_brief(), _pass(), baseline_tree="abc", current_tree="abc")
    assert not outcome.accepted
    assert "unchanged tree" in outcome.refusals[0]


def test_a_pass_with_a_changed_tree_is_accepted():
    assert check_result(_brief(), _pass(), baseline_tree="abc", current_tree="def").accepted


def test_an_unknowable_tree_hash_produces_no_finding():
    """None means 'nothing to compare', never 'unchanged'."""
    assert check_result(_brief(), _pass(), baseline_tree=None, current_tree="x").accepted
    assert check_result(_brief(), _pass(), baseline_tree="x", current_tree=None).accepted


def test_a_read_only_task_is_not_asked_to_move_the_tree():
    brief = _brief(role="docs", writes=False)
    assert check_result(brief, _pass(), baseline_tree="a", current_tree="a").accepted


# --- rule 4: ownership ----------------------------------------------------


def test_writing_outside_ownership_is_refused_and_the_paths_are_named():
    brief = _brief(ownership=("src/auth/**",))
    result = _pass(changed_paths=("src/auth/a.py", "src/db/b.py"))
    outcome = check_result(brief, result, baseline_tree="a", current_tree="b")
    assert not outcome.accepted
    assert "src/db/b.py" in outcome.refusals[0]
    assert "src/auth/a.py" not in outcome.refusals[0]


def test_writing_inside_ownership_is_accepted():
    brief = _brief(ownership=("src/auth/**",))
    result = _pass(changed_paths=("src/auth/a.py",))
    assert check_result(brief, result, baseline_tree="a", current_tree="b").accepted


# --- refusal shape --------------------------------------------------------


def test_a_refused_pass_is_downgraded_rather_than_raised():
    outcome = check_result(_brief(role="test"), _pass(evidence=""))
    assert outcome.result.verdict is Verdict.FAIL
    assert "refused by the honesty gates" in outcome.result.summary


def test_a_refusal_preserves_what_the_worker_claimed():
    outcome = check_result(_brief(role="test"), _pass(evidence="", summary="all good"))
    assert "worker said: all good" in outcome.result.summary


def test_all_applicable_refusals_are_reported_not_just_the_first():
    brief = _brief(ownership=("src/auth/**",))
    result = _pass(checks="", changed_paths=("elsewhere.py",))
    outcome = check_result(brief, result, baseline_tree="a", current_tree="a")
    assert len(outcome.refusals) == 3


def test_a_non_pass_verdict_passes_through_untouched():
    """The gates check a claim of success. A failure has made no claim."""
    fail = TaskResult(task_id="t1", verdict=Verdict.FAIL, summary="broke")
    outcome = check_result(_brief(), fail, baseline_tree="a", current_tree="a")
    assert outcome.accepted and outcome.result is fail


# --- tree_hash ------------------------------------------------------------


def test_tree_hash_moves_when_the_working_tree_changes(tmp_path):
    repo = _git_repo(tmp_path)
    before = tree_hash(repo)
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert before is not None and tree_hash(repo) != before


def test_tree_hash_moves_on_a_commit_that_leaves_the_tree_clean(tmp_path):
    """HEAD is in the hash so committing is distinguishable from doing nothing."""
    repo = _git_repo(tmp_path)
    (repo / "new.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
    dirty = tree_hash(repo)
    subprocess.run(["git", "commit", "-qm", "work"], cwd=repo, capture_output=True, check=True)
    assert tree_hash(repo) != dirty


def test_tree_hash_degrades_to_none_outside_a_repository(tmp_path):
    assert tree_hash(tmp_path / "not-a-repo") is None


def test_tree_hash_degrades_to_none_with_no_directory():
    assert tree_hash(None) is None


def test_tree_hash_degrades_to_none_when_git_cannot_run(monkeypatch, tmp_path):
    def boom(*_a, **_k):
        raise OSError("git missing")
    monkeypatch.setattr(subprocess, "run", boom)
    assert tree_hash(tmp_path) is None


# --- the worker seam ------------------------------------------------------


def test_mock_worker_records_every_brief_it_is_handed():
    worker = MockWorker()
    worker.run(_brief())
    assert [b.task_id for b in worker.briefs] == ["t1"]
    assert isinstance(worker, Worker)


def test_mock_worker_plays_a_script_in_order():
    scripted = {"t1": [
        TaskResult(task_id="t1", verdict=Verdict.FAIL, summary="first"),
        TaskResult(task_id="t1", verdict=Verdict.PASS, summary="second"),
    ]}
    worker = MockWorker(scripted=scripted)
    assert worker.run(_brief()).summary == "first"
    assert worker.run(_brief()).summary == "second"
    assert worker.run(_brief()).summary.startswith("mock worker ran")


def test_execute_runs_the_gates_against_whatever_the_worker_returned(tmp_path):
    """A worker that reports a pass without touching the repo is refused."""
    repo = _git_repo(tmp_path)
    worker = MockWorker(scripted={"t1": [
        TaskResult(task_id="t1", verdict=Verdict.PASS, summary="did nothing",
                   evidence="e", checks="c", usage=Usage(cost_usd=0.1))
    ]})
    result, outcome = execute(_brief(), worker, project_dir=repo)
    assert result.verdict is Verdict.FAIL
    assert any("unchanged tree" in r for r in outcome.refusals)


def test_execute_accepts_a_pass_that_actually_changed_the_tree(tmp_path):
    repo = _git_repo(tmp_path)

    class Writing:
        def run(self, brief):
            (repo / "written.py").write_text("x = 1\n", encoding="utf-8")
            return _pass(changed_paths=("written.py",))

    result, outcome = execute(_brief(ownership=("**",)), Writing(), project_dir=repo)
    assert outcome.accepted and result.verdict is Verdict.PASS


def test_execute_skips_the_tree_check_for_a_read_only_task(tmp_path):
    repo = _git_repo(tmp_path)
    brief = _brief(role="review", writes=False)
    result, outcome = execute(brief, MockWorker(), project_dir=repo)
    assert outcome.accepted and result.verdict is Verdict.PASS


def test_execute_tolerates_a_project_dir_that_is_not_a_repo(tmp_path):
    result, outcome = execute(_brief(), MockWorker(), project_dir=tmp_path)
    assert outcome.accepted, "an unknowable baseline must not manufacture a refusal"
    assert result.verdict is Verdict.PASS


@pytest.mark.parametrize("role", ["test", "verify", "qa"])
def test_every_evidence_role_is_gated(role):
    assert not check_result(_brief(role=role), _pass(evidence="")).accepted


# --- rule 3: the hash has to see content, not git's summary of it ---------


def test_a_second_edit_to_an_already_dirty_file_moves_the_tree_hash(tmp_path):
    """`git status --porcelain` prints ` M app.py` no matter how much changed.

    Hashing that line means the second task to touch a file another task left
    dirty reads as "nothing was written" — the refusal that stops a run dead.
    """
    repo = _git_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("one\n", encoding="utf-8")
    dirty = tree_hash(repo)

    app.write_text("one\ntwo\n", encoding="utf-8")

    assert tree_hash(repo) != dirty


def test_editing_an_untracked_file_twice_moves_the_tree_hash(tmp_path):
    """`?? new.py` is likewise identical across two writes to a new file."""
    repo = _git_repo(tmp_path)
    new = repo / "new.py"
    new.write_text("first\n", encoding="utf-8")
    first = tree_hash(repo)

    new.write_text("first\nsecond\n", encoding="utf-8")

    assert tree_hash(repo) != first


def test_a_staged_edit_that_is_then_extended_moves_the_tree_hash(tmp_path):
    """Staged content moves the index, so the hash must read the index too."""
    repo = _git_repo(tmp_path)
    app = repo / "app.py"
    app.write_text("one\n", encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=repo, capture_output=True, check=True)
    staged = tree_hash(repo)

    app.write_text("one\ntwo\n", encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=repo, capture_output=True, check=True)

    assert tree_hash(repo) != staged


def test_the_tree_hash_is_stable_when_truly_nothing_changed(tmp_path):
    """The rule only works if the hash is quiet on a tree that did not move."""
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text("one\n", encoding="utf-8")
    assert tree_hash(repo) == tree_hash(repo)
