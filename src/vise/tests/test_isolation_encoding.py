"""A repository is allowed to hold bytes Python cannot decode as UTF-8.

`_git` ran every git command with `text=True`, which decodes strict. git only
base64-encodes a diff for files it considers *binary* — meaning NUL-containing —
so a latin-1 source file with no NUL is diffed as text and its raw bytes reach
the decoder. `UnicodeDecodeError` is a `ValueError`, which is not in the
`(OSError, SubprocessError)` the helper catches, so it left `_git` — whose whole
contract is "report, never raise" — and then left `Scheduler.run`, skipping
`cleanup()` and stranding a worktree and a branch per attempt.

The run does not have to *succeed* on such a file for these tests to pass. It has
to not take the process down and not leak.
"""
from __future__ import annotations

import subprocess

import pytest

from vise.runtime.isolation import WorktreePool


def _repo(tmp_path):
    def run(*args):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return tmp_path


LATIN1 = "café naïve résumé\n".encode("latin-1")


def test_a_latin1_source_file_integrates_instead_of_crashing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.py").write_bytes(b"# " + LATIN1)
    subprocess.run(("git", "add", "-A"), cwd=repo, capture_output=True, check=True)
    subprocess.run(("git", "commit", "-qm", "latin1"), cwd=repo,
                   capture_output=True, check=True)

    pool = WorktreePool.create(repo, tmp_path / "iso", "r")
    work = pool.acquire("t")
    (work / "app.py").write_bytes(b"# " + LATIN1 + b"# more " + LATIN1)

    result = pool.integrate("t")

    assert result.applied, result.reason
    assert (repo / "app.py").read_bytes() == (work / "app.py").read_bytes()
    pool.cleanup()


def test_a_worktree_holding_undecodable_bytes_is_still_cleaned_up(tmp_path):
    """The leak is the part that compounds: one stranded worktree per attempt."""
    repo = _repo(tmp_path)
    pool = WorktreePool.create(repo, tmp_path / "iso", "r")
    work = pool.acquire("t")
    (work / "new.py").write_bytes(LATIN1)

    pool.integrate("t")
    pool.cleanup()

    assert pool.worktrees == {}
    branches = subprocess.run(("git", "branch", "--format=%(refname:short)"),
                              cwd=repo, capture_output=True, text=True).stdout
    assert "vise/" not in branches, f"a branch was stranded: {branches!r}"


def test_changed_paths_survives_an_undecodable_filename(tmp_path):
    """Paths, not only contents, can carry bytes that are not UTF-8."""
    repo = _repo(tmp_path)
    pool = WorktreePool.create(repo, tmp_path / "iso", "r")
    work = pool.acquire("t")
    try:
        (work / b"caf\xe9.py".decode("utf-8", "surrogateescape")).write_text("x\n")
    except (OSError, UnicodeEncodeError):
        pytest.skip("this filesystem refuses non-UTF-8 filenames")

    assert isinstance(pool.changed_paths("t"), tuple)
    pool.cleanup()
