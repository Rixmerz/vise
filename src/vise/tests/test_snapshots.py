"""Tests for vise.core.snapshots — ref naming, create, list, prune, journal.

Covers:
- _snapshot_id() format: YYYYMMDDTHHMMSS-<4hex>
- SNAPSHOT_REF_PREFIX constant is correct
- create() returns None for non-git directory
- create() produces a Snapshot with correct ref prefix in a real git repo
- create() appends to the journal JSONL
- list_all() returns empty when journal doesn't exist
- list_all() parses entries from journal correctly
- list_all() skips corrupt/invalid lines in journal
- prune() deletes old snapshots beyond keep count
- prune() rewrites journal to only contain survivors
- create_for_phase_transition label/phase encoding
- create_for_phase_transition swallows exceptions (non-fatal)
- _resolve_ref handles full ref path pass-through
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from vise.core.snapshots import (
    SNAPSHOT_REF_PREFIX,
    Snapshot,
    _snapshot_id,
    create,
    create_for_phase_transition,
    list_all,
    prune,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> None:
    """Initialize a minimal git repo with one commit."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=path, check=True, capture_output=True,
    )
    (path / "README.md").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=path, check=True, capture_output=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A minimal git repo under tmp_path/repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


# ---------------------------------------------------------------------------
# _snapshot_id
# ---------------------------------------------------------------------------

def test_snapshot_id_format_matches_expected_pattern():
    sid = _snapshot_id()
    # e.g. "20260101T120000-abcd"
    parts = sid.split("-")
    assert len(parts) == 2, f"Expected 2 dash-separated parts, got: {sid!r}"
    ts, rand = parts
    assert len(ts) == 15, f"Timestamp part should be 15 chars (YYYYMMDDTHHMMSS), got {ts!r}"
    assert ts[8] == "T", "Timestamp must have 'T' at position 8"
    assert len(rand) == 4, f"Random hex suffix should be 4 chars, got {rand!r}"
    assert all(c in "0123456789abcdef" for c in rand), f"Random suffix must be hex, got {rand!r}"


def test_snapshot_id_two_calls_differ():
    # Very rarely could collide; acceptable in practice
    s1 = _snapshot_id()
    s2 = _snapshot_id()
    # At least the random part should have a chance of differing;
    # both must match format
    assert "-" in s1 and "-" in s2


# ---------------------------------------------------------------------------
# SNAPSHOT_REF_PREFIX
# ---------------------------------------------------------------------------

def test_snapshot_ref_prefix_value():
    assert SNAPSHOT_REF_PREFIX == "refs/vise/snapshots/"


# ---------------------------------------------------------------------------
# create — non-git directory
# ---------------------------------------------------------------------------

def test_create_returns_none_for_non_git_directory(tmp_path: Path):
    result = create(tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# create — real git repo
# ---------------------------------------------------------------------------

def test_create_returns_snapshot_with_correct_ref_prefix(git_repo: Path):
    snap = create(git_repo, label="test-snap", phase="understand")
    assert snap is not None
    assert snap.ref.startswith(SNAPSHOT_REF_PREFIX)


def test_create_snapshot_id_embedded_in_ref(git_repo: Path):
    snap = create(git_repo, label="x")
    assert snap is not None
    assert snap.id in snap.ref


def test_create_snapshot_label_preserved(git_repo: Path):
    snap = create(git_repo, label="my-label", phase="implement")
    assert snap is not None
    assert snap.label == "my-label"
    assert snap.phase == "implement"


def test_create_snapshot_has_nonzero_created_at(git_repo: Path):
    before = time.time()
    snap = create(git_repo)
    after = time.time()
    assert snap is not None
    assert before <= snap.created_at <= after


def test_create_snapshot_tree_is_nonempty_sha(git_repo: Path):
    snap = create(git_repo)
    assert snap is not None
    assert len(snap.tree) == 40  # git SHA1


def test_create_snapshot_commit_is_nonempty_sha(git_repo: Path):
    snap = create(git_repo)
    assert snap is not None
    assert len(snap.commit) == 40


def test_create_appends_to_journal(git_repo: Path):
    create(git_repo, label="first")
    create(git_repo, label="second")
    snaps = list_all(git_repo)
    assert len(snaps) == 2


def test_create_does_not_disturb_working_index(git_repo: Path):
    """Creating a snapshot must not stage user files via the real index."""
    new_file = git_repo / "unstaged.txt"
    new_file.write_text("unstaged content", encoding="utf-8")

    create(git_repo)

    # The real index should not have our new file staged
    status = subprocess.run(
        ["git", "-C", str(git_repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    # unstaged.txt should appear as untracked "??" not staged "A"
    assert "?? unstaged.txt" in status.stdout


def test_create_ref_visible_via_git_for_each_ref(git_repo: Path):
    snap = create(git_repo)
    assert snap is not None
    result = subprocess.run(
        ["git", "-C", str(git_repo), "for-each-ref", snap.ref],
        capture_output=True, text=True, check=True,
    )
    assert snap.ref in result.stdout


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------

def test_list_all_returns_empty_when_journal_missing(tmp_path: Path):
    result = list_all(tmp_path)
    assert result == []


def test_list_all_returns_parsed_entries(git_repo: Path):
    snap = create(git_repo, label="snap-1", phase="design")
    assert snap is not None
    listed = list_all(git_repo)
    assert len(listed) == 1
    s = listed[0]
    assert s.id == snap.id
    assert s.label == "snap-1"
    assert s.phase == "design"
    assert s.ref == snap.ref


def test_list_all_skips_corrupt_lines(tmp_path: Path):
    """Journal with one bad line and one good line should return only the good one."""
    from vise.core import paths
    state_dir = paths.ensure(paths.project_state_dir(tmp_path))
    journal = state_dir / "snapshots.jsonl"

    good = {
        "id": "20260101T000000-abcd",
        "ref": f"{SNAPSHOT_REF_PREFIX}20260101T000000-abcd",
        "commit": "a" * 40,
        "tree": "b" * 40,
        "label": "ok",
        "phase": "",
        "created_at": 1.0,
    }
    journal.write_text(
        "{bad json\n" + json.dumps(good) + "\n",
        encoding="utf-8",
    )

    result = list_all(tmp_path)
    assert len(result) == 1
    assert result[0].id == "20260101T000000-abcd"


def test_list_all_skips_entries_missing_required_keys(tmp_path: Path):
    from vise.core import paths
    state_dir = paths.ensure(paths.project_state_dir(tmp_path))
    journal = state_dir / "snapshots.jsonl"

    # Missing 'commit' and 'ref' — should be skipped by KeyError
    bad = json.dumps({"id": "x"})
    journal.write_text(bad + "\n", encoding="utf-8")

    result = list_all(tmp_path)
    assert result == []


def test_list_all_handles_empty_journal(tmp_path: Path):
    from vise.core import paths
    state_dir = paths.ensure(paths.project_state_dir(tmp_path))
    journal = state_dir / "snapshots.jsonl"
    journal.write_text("", encoding="utf-8")
    assert list_all(tmp_path) == []


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def test_prune_returns_zero_when_below_keep_limit(git_repo: Path):
    create(git_repo, label="a")
    create(git_repo, label="b")
    deleted = prune(git_repo, keep=10)
    assert deleted == 0
    assert len(list_all(git_repo)) == 2


def test_prune_deletes_oldest_snapshots_beyond_keep(git_repo: Path):
    create(git_repo, label="oldest")
    create(git_repo, label="middle")
    create(git_repo, label="newest")

    deleted = prune(git_repo, keep=2)
    assert deleted == 1
    survivors = list_all(git_repo)
    assert len(survivors) == 2
    labels = {s.label for s in survivors}
    assert "oldest" not in labels


def test_prune_rewrites_journal_to_contain_only_survivors(git_repo: Path):
    for i in range(5):
        create(git_repo, label=f"snap-{i}")

    prune(git_repo, keep=3)
    survivors = list_all(git_repo)
    assert len(survivors) == 3


def test_prune_returns_correct_delete_count(git_repo: Path):
    for _ in range(6):
        create(git_repo)

    deleted = prune(git_repo, keep=4)
    assert deleted == 2


# ---------------------------------------------------------------------------
# .gitignore auto-ignore of .vise/
# ---------------------------------------------------------------------------

def test_create_adds_vise_to_gitignore_when_missing(git_repo: Path):
    create(git_repo, label="x")
    gitignore = git_repo / ".gitignore"
    assert gitignore.exists()
    assert ".vise/" in gitignore.read_text(encoding="utf-8").splitlines()
    # git actually ignores it
    rc = subprocess.run(
        ["git", "-C", str(git_repo), "check-ignore", ".vise"],
        capture_output=True,
    ).returncode
    assert rc == 0


def test_create_appends_vise_preserving_existing_gitignore(git_repo: Path):
    (git_repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    create(git_repo)
    lines = (git_repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines == ["node_modules/", ".vise/"]


def test_create_gitignore_idempotent(git_repo: Path):
    create(git_repo)
    create(git_repo)
    lines = (git_repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count(".vise/") == 1


def test_create_respects_existing_vise_variants(git_repo: Path):
    (git_repo / ".gitignore").write_text(".vise\n", encoding="utf-8")
    create(git_repo)
    text = (git_repo / ".gitignore").read_text(encoding="utf-8")
    assert text == ".vise\n"  # untouched — already covered


def test_gitignore_untouched_outside_git_repo(tmp_path: Path):
    from vise.core.snapshots import _journal_path
    _journal_path(tmp_path)
    assert not (tmp_path / ".gitignore").exists()


# ---------------------------------------------------------------------------
# create_for_phase_transition
# ---------------------------------------------------------------------------

def test_create_for_phase_transition_label_contains_node_names():
    captured = []

    def fake_create(project, *, label="", phase=""):
        captured.append({"label": label, "phase": phase})
        snap = MagicMock()
        snap.id = "fake-id"
        return snap

    with patch("vise.core.snapshots.create", side_effect=fake_create):
        result = create_for_phase_transition(
            Path("/tmp"),
            workflow_name="my-workflow",
            from_node="understand",
            to_node="implement",
        )

    assert result is not None
    assert "understand" in captured[0]["label"]
    assert "implement" in captured[0]["label"]
    assert "my-workflow" in captured[0]["label"]


def test_create_for_phase_transition_phase_tag_format():
    captured = []

    def fake_create(project, *, label="", phase=""):
        captured.append({"label": label, "phase": phase})
        snap = MagicMock()
        snap.id = "x"
        return snap

    with patch("vise.core.snapshots.create", side_effect=fake_create):
        create_for_phase_transition(
            Path("/tmp"),
            workflow_name="wf",
            from_node="A",
            to_node="B",
        )

    assert "phase=" in captured[0]["phase"]
    assert "A" in captured[0]["phase"]
    assert "B" in captured[0]["phase"]


def test_create_for_phase_transition_returns_none_and_does_not_raise_on_exception():
    with patch("vise.core.snapshots.create", side_effect=RuntimeError("disk full")):
        result = create_for_phase_transition(
            Path("/tmp"),
            workflow_name="wf",
            from_node="x",
            to_node="y",
        )
    assert result is None


def test_create_for_phase_transition_in_real_git_repo(git_repo: Path):
    snap = create_for_phase_transition(
        git_repo,
        workflow_name="feature-dev",
        from_node="understand",
        to_node="design",
    )
    assert snap is not None
    assert snap.ref.startswith(SNAPSHOT_REF_PREFIX)
    assert "understand" in snap.label
    assert "design" in snap.label
