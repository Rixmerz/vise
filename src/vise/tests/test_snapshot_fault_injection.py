"""Snapshots against a repository git is happy with and Python is not.

The same class of fault as `isolation._git`, in the module that is supposed to
be the safety net: `_git` ran with `text=True`, which decodes strict and has no
timeout. A single latin-1 tracked file — an old source file, a fixture, a
vendored dependency — turns `snapshot_diff` and the restore preview into a
`UnicodeDecodeError` raised out of the MCP tool, where the caller catches only
`RuntimeError`.

A snapshot layer that raises on a repository it is meant to protect is worse
than one that is absent: the user reached for it precisely because something
had gone wrong.
"""
from __future__ import annotations

import subprocess

import pytest

from vise.core import snapshots

LATIN1 = "café naïve\n".encode("latin-1")


def _repo(tmp_path):
    def run(*args):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return run


def test_diff_survives_a_latin1_tracked_file(tmp_path):
    run = _repo(tmp_path)
    target = tmp_path / "l.txt"
    target.write_bytes(LATIN1)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "latin1")

    first = snapshots.create(tmp_path, label="before")
    target.write_bytes(LATIN1 + b"more caf\xe9\n")
    second = snapshots.create(tmp_path, label="after")

    out = snapshots.diff(tmp_path, first.id, second.id)

    assert isinstance(out, str)
    assert "l.txt" in out


def test_the_restore_preview_survives_a_latin1_tracked_file(tmp_path):
    run = _repo(tmp_path)
    target = tmp_path / "l.txt"
    target.write_bytes(LATIN1)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "latin1")

    snap = snapshots.create(tmp_path, label="before")
    target.write_bytes(LATIN1 + b"edited by hand caf\xe9\n")

    preview = snapshots.restore(tmp_path, snap.id, dry_run=True)

    assert isinstance(preview, str)
    assert "l.txt" in preview


def test_a_snapshot_of_a_latin1_file_restores_it_byte_for_byte(tmp_path):
    """Reading it is not enough; the bytes have to survive the round trip."""
    run = _repo(tmp_path)
    target = tmp_path / "l.txt"
    target.write_bytes(LATIN1)
    run("git", "add", "-A")
    run("git", "commit", "-qm", "latin1")

    snap = snapshots.create(tmp_path, label="before")
    target.write_bytes(b"replaced\n")

    snapshots.restore(tmp_path, snap.id, dry_run=False)

    assert target.read_bytes() == LATIN1


def test_a_latin1_filename_does_not_break_a_snapshot(tmp_path):
    """Paths carry bytes too, and git quotes them rather than failing."""
    run = _repo(tmp_path)
    try:
        (tmp_path / b"caf\xe9.txt".decode("utf-8", "surrogateescape")).write_text("x\n")
    except (OSError, UnicodeEncodeError):
        pytest.skip("this filesystem refuses non-UTF-8 filenames")
    run("git", "add", "-A")

    snap = snapshots.create(tmp_path, label="odd name")

    assert snap is not None and snap.ref


def test_git_calls_carry_a_timeout():
    """A hung git is a hung MCP server; nothing here may wait forever."""
    import inspect

    src = inspect.getsource(snapshots)
    calls = src.count("subprocess.run(")
    timeouts = src.count("timeout=")
    assert timeouts >= calls, (
        f"{calls} subprocess.run calls and only {timeouts} timeout= arguments"
    )
