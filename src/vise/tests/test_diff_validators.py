"""Two promises that were written in prose and checked by nothing.

`ponytail` says a new dependency needs a stated reason why the stdlib and the
existing manifest could not do it. `skills/orchestration` says a wave must
partition scope by file ownership before dispatching. Both were preferences an
agent could read and ignore, because no gate could read them at all.

`no_new_deps` and `diff_scope` are those two promises as validators. Neither
bans anything: `no_new_deps` passes when the addition is declared in `allow`
(naming it IS the stated reason), and `diff_scope` passes when the file is
inside the partition the node declared.

The interesting cases here are the edges, because a validator is only as good
as what it does when it cannot check:

  - No git, no manifests, an unresolvable base ref -> `unverified`, never a
    block. Same fail-open contract as every other validator in this engine.
  - An empty `allow` on `diff_scope` -> FAIL CLOSED. A scope gate that permits
    everything when misconfigured is worse than no gate, because it reads as
    one. `quality_check` makes the identical choice for an empty `check:`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vise.engines.goal_state import Goal
from vise.engines.validators import (
    DiffScopeValidator,
    NoNewDepsValidator,
    build_validators,
)


def _goal(project_dir: Path) -> Goal:
    return Goal(
        id="test-goal",
        project_dir=str(project_dir),
        goal="test goal",
        acceptance_criteria=[],
        target_confidence=0.9,
        complexity="unknown",
        status="active",
        started_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "pyproject.toml").write_text(
        "[project]\nname = 'x'\ndependencies = [\n  'requests',\n]\n"
    )
    (r / "src").mkdir()
    (r / "src" / "app.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "initial")
    return r


# ---------------------------------------------------------------------------
# no_new_deps
# ---------------------------------------------------------------------------

def test_clean_tree_passes_and_says_what_it_checked(repo: Path):
    rec = NoNewDepsValidator().run(_goal(repo))
    assert rec.passed
    assert rec.outcome == "verified"
    assert "manifest" in rec.evidence


def test_an_added_dependency_fails_and_names_it(repo: Path):
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\ndependencies = [\n  'requests',\n  'httpx',\n]\n"
    )
    rec = NoNewDepsValidator().run(_goal(repo))

    assert not rec.passed
    assert rec.outcome == "failed"
    assert "httpx" in rec.evidence, "the report has to name the package, not just the count"


def test_a_declared_dependency_passes(repo: Path):
    """`allow` is the stated reason. Naming it in the node config is the declaration."""
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\ndependencies = [\n  'requests',\n  'httpx',\n]\n"
    )
    rec = NoNewDepsValidator(allow=("httpx",)).run(_goal(repo))
    assert rec.passed


def test_editing_code_is_not_a_dependency_change(repo: Path):
    (repo / "src" / "app.py").write_text("x = 1\ny = 2\n")
    assert NoNewDepsValidator().run(_goal(repo)).passed


def test_a_lockfile_addition_counts(repo: Path):
    """A transitive add is still a new trust relationship, and nobody declares it."""
    (repo / "uv.lock").write_text('name = "x"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "lock")
    (repo / "uv.lock").write_text('name = "x"\n"leftpad" = "1.0"\n')

    rec = NoNewDepsValidator().run(_goal(repo))
    assert not rec.passed
    assert "leftpad" in rec.evidence


def test_comments_and_headers_are_not_dependencies(repo: Path):
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n# a new comment\n\n[tool.ruff]\ndependencies = [\n  'requests',\n]\n"
    )
    assert NoNewDepsValidator().run(_goal(repo)).passed


def test_no_git_is_unverified_not_blocked(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    rec = NoNewDepsValidator().run(_goal(plain))

    assert rec.passed, "never block a repo for not being a git repo"
    assert rec.outcome == "unverified", "and never let that pass read as clean"
    assert rec.source == "asserted"


def test_a_repo_with_no_manifest_is_unverified(tmp_path: Path):
    r = tmp_path / "bare"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@e.com")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("hi")
    _git(r, "add", "-A")
    subprocess.run(["git", "commit", "-qm", "i"], cwd=r, check=True, capture_output=True)

    rec = NoNewDepsValidator().run(_goal(r))
    assert rec.passed and rec.outcome == "unverified"
    assert "no dependency manifest" in rec.evidence


def test_an_unresolvable_base_is_unverified(repo: Path):
    rec = NoNewDepsValidator(base="no-such-ref").run(_goal(repo))
    assert rec.passed and rec.outcome == "unverified"
    assert "no-such-ref" in rec.evidence


# ---------------------------------------------------------------------------
# diff_scope
# ---------------------------------------------------------------------------

def test_changes_inside_scope_pass(repo: Path):
    (repo / "src" / "app.py").write_text("x = 2\n")
    rec = DiffScopeValidator(allow=("src/*",)).run(_goal(repo))
    assert rec.passed, rec.evidence


def test_a_change_outside_scope_fails_and_names_the_file(repo: Path):
    (repo / "src" / "app.py").write_text("x = 2\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'y'\n")

    rec = DiffScopeValidator(allow=("src/*",)).run(_goal(repo))
    assert not rec.passed
    assert "pyproject.toml" in rec.evidence


def test_a_new_untracked_file_outside_scope_is_caught(repo: Path):
    """`git diff` never sees an untracked file — the exact case worth catching."""
    (repo / "sneaky.py").write_text("print('hi')\n")

    rec = DiffScopeValidator(allow=("src/*",)).run(_goal(repo))
    assert not rec.passed
    assert "sneaky.py" in rec.evidence


def test_empty_allow_fails_closed(repo: Path):
    """A scope gate that permits everything when misconfigured reads as a gate."""
    (repo / "src" / "app.py").write_text("x = 2\n")
    rec = DiffScopeValidator().run(_goal(repo))

    assert not rec.passed
    assert "misconfigured" in rec.evidence


def test_no_changes_is_unverified(repo: Path):
    """Nothing to check is not the same claim as checked-and-clean."""
    rec = DiffScopeValidator(allow=("src/*",)).run(_goal(repo))
    assert rec.passed and rec.outcome == "unverified"


def test_diff_scope_without_git_is_unverified(tmp_path: Path):
    plain = tmp_path / "plain2"
    plain.mkdir()
    rec = DiffScopeValidator(allow=("src/*",)).run(_goal(plain))
    assert rec.passed and rec.outcome == "unverified"


def test_globs_match_nested_paths(repo: Path):
    nested = repo / "src" / "deep" / "inner"
    nested.mkdir(parents=True)
    (nested / "mod.py").write_text("z = 1\n")

    assert DiffScopeValidator(allow=("src/**",)).run(_goal(repo)).passed
    assert not DiffScopeValidator(allow=("docs/**",)).run(_goal(repo)).passed


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_both_are_reachable_from_a_graph_node():
    """A validator absent from the registry builds as UnknownValidator and blocks."""
    built = build_validators([
        {"type": "no_new_deps", "weight": 0.4, "allow": ["httpx"]},
        {"type": "diff_scope", "weight": 0.5, "allow": ["src/*"]},
    ])
    assert [v.name for v in built] == ["no_new_deps", "diff_scope"]
    assert built[0].allow == ["httpx"]
    assert built[1].weight == 0.5
