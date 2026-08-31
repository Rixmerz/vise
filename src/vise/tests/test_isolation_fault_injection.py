"""What the worktree layer does when git does not cooperate.

Tier T2 of the audit's plan, and the tier chosen first because this is the layer
where vise loses the user's work rather than merely reporting it wrongly. Every
test here injects a fault a real repository can produce — an ignored path, a
symlink, a dirty working tree, two task ids that collide, a run that stops
mid-flight — and asserts what survives.

The module's own docstring makes two promises these check literally:

* *"Integration reports conflicts; it does not resolve them."* A conflict means
  two tasks changed the same lines. Anything else git refuses is a different
  outcome and has to say so, or the ownership declaration gets blamed for the
  user's uncommitted edit.
* *"On any failure the main tree is left exactly as it was."* Exactly as it was
  includes the index, the symlinks, and the file modes — not just the bytes of
  regular files.
"""
from __future__ import annotations

import os
import subprocess

from vise.runtime.isolation import WorktreePool, _slug


def _repo(tmp_path):
    def run(*args, cwd=tmp_path):
        subprocess.run(args, cwd=cwd, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("\n".join(f"line {i}" for i in range(40)) + "\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return run


def _pool(tmp_path):
    return WorktreePool.create(tmp_path, tmp_path / "iso", "r")


def _porcelain(repo) -> str:
    return subprocess.run(("git", "status", "--porcelain"), cwd=repo,
                          capture_output=True, text=True).stdout


# --- G1: a failed integration must not poison the tasks after it ----------


def test_a_failed_integration_does_not_block_a_later_task_on_the_same_file(tmp_path):
    """`--3way` implies `--index`, so a success leaves its paths staged.

    Backing out a *later* failure with `git reset -- <paths>` resets those
    entries to HEAD while the worktree keeps the post-integration bytes. Index
    and worktree then disagree, and every subsequent task touching the file is
    refused for a reason that has nothing to do with it.
    """
    _repo(tmp_path)
    pool = _pool(tmp_path)

    # Both acquired before either integrates, so B's base is the original file
    # rather than A's result — a worktree opened later is seeded with what the
    # run has already landed, which is exactly what stops them colliding.
    a = pool.acquire("a")
    b = pool.acquire("b")

    lines = (a / "f.txt").read_text().split("\n")
    lines[0] = "A owns line 1"
    (a / "f.txt").write_text("\n".join(lines))
    assert pool.integrate("a").applied
    after_a = _porcelain(tmp_path)

    lines = (b / "f.txt").read_text().split("\n")
    lines[0] = "B owns line 1"
    (b / "f.txt").write_text("\n".join(lines))
    assert not pool.integrate("b").applied, "this test needs B to fail"

    assert _porcelain(tmp_path) == after_a, (
        "backing out B changed the state A had legitimately left behind"
    )

    c = pool.acquire("c")
    text = (c / "f.txt").read_text()
    (c / "f.txt").write_text(text + "C appends\n")
    result = pool.integrate("c")

    assert result.applied, f"C was refused after B's failure: {result.reason}"
    pool.cleanup()


# --- G2: writes to ignored paths are still writes ------------------------


def test_a_write_to_a_gitignored_path_is_not_reported_as_no_change(tmp_path):
    """`git add -A` honours .gitignore, so the diff never sees `dist/`.

    An empty diff was read as "the task changed nothing", reported
    `applied=True`, and the scheduler then released the worktree — which
    force-removes it. The work is gone and the run says it succeeded.
    """
    run = _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("dist/\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "ignore dist")

    pool = _pool(tmp_path)
    work = pool.acquire("t")
    (work / "dist").mkdir()
    (work / "dist" / "out.js").write_text("console.log(1)\n")

    result = pool.integrate("t")

    assert not result.applied, (
        "a task that wrote a file was told it changed nothing, and its "
        "worktree is about to be deleted"
    )
    assert "dist/out.js" in (result.reason + " ".join(result.changed_paths))
    pool.cleanup()


# --- G3: the user's own edits are not a conflict -------------------------


def test_a_users_unrelated_edit_to_the_same_file_does_not_block_integration(tmp_path):
    """`--3way` implies `--index`, and git checks index-vs-worktree first.

    So an uncommitted human edit anywhere in a file the task touched is refused
    before any merge is attempted — and reported as though two tasks had
    collided, which blames an ownership declaration for the user's own work.
    """
    _repo(tmp_path)
    pool = _pool(tmp_path)

    work = pool.acquire("t")
    lines = (work / "f.txt").read_text().split("\n")
    lines[30] = "task edits line 30"
    (work / "f.txt").write_text("\n".join(lines))

    user = (tmp_path / "f.txt").read_text().split("\n")
    user[0] = "the user is editing line 1 right now"
    (tmp_path / "f.txt").write_text("\n".join(user))

    result = pool.integrate("t")

    assert result.applied, f"the user's unrelated edit blocked the task: {result.reason}"
    landed = (tmp_path / "f.txt").read_text()
    assert "the user is editing line 1 right now" in landed, "the user's edit was lost"
    assert "task edits line 30" in landed
    pool.cleanup()


def test_a_real_conflict_is_still_reported_as_one(tmp_path):
    """The guard for the fix above: loosening this must not lose the finding."""
    _repo(tmp_path)
    pool = _pool(tmp_path)

    work = pool.acquire("t")
    lines = (work / "f.txt").read_text().split("\n")
    lines[0] = "the task rewrote line 1"
    (work / "f.txt").write_text("\n".join(lines))

    user = (tmp_path / "f.txt").read_text().split("\n")
    user[0] = "the user rewrote line 1 differently"
    (tmp_path / "f.txt").write_text("\n".join(user))

    result = pool.integrate("t")

    assert not result.applied
    assert "the user rewrote line 1 differently" in (tmp_path / "f.txt").read_text(), (
        "a refused integration must leave the user's edit alone"
    )
    pool.cleanup()


# --- G4: two task ids that slug alike ------------------------------------


def test_two_task_ids_that_slug_alike_get_different_trees(tmp_path):
    """`.` and `/` both collapse to `-`, so `api.v2` and `api/v2` collide.

    `acquire` force-removes whatever is already at the path — which is the
    first task's worktree, while it is running.
    """
    _repo(tmp_path)
    pool = _pool(tmp_path)

    a = pool.acquire("api.v2")
    (a / "marker.txt").write_text("A was here\n")
    b = pool.acquire("api/v2")

    assert a != b, f"both ids resolved to {a}"
    assert (a / "marker.txt").exists(), "the first task's worktree was destroyed"
    assert pool.branch_for("api.v2") != pool.branch_for("api/v2")
    pool.cleanup()


def test_a_slug_is_still_a_safe_path_and_refname(tmp_path):
    """Making it injective must not make it unsafe."""
    for raw in ("../escape", "a/../../b", "api.v2", "with space", "..", "///"):
        out = _slug(raw)
        assert ".." not in out
        assert "/" not in out
        assert out and not out.startswith("-") and not out.endswith("-")


# --- G5: a run that stops mid-flight -------------------------------------


def test_cleanup_preserves_the_work_of_a_task_that_never_integrated(tmp_path):
    """A patch is only ever written inside `integrate`.

    A cancelled task never reaches it, so `cleanup()` force-removes a worktree
    holding real work with no record of it anywhere. That is the same money the
    ledger now correctly reports having spent.
    """
    _repo(tmp_path)
    pool = _pool(tmp_path)

    work = pool.acquire("t")
    (work / "new.py").write_text("# work nobody collected\n")

    pool.cleanup()

    patches = list((tmp_path / "iso" / "patches").glob("*"))
    assert patches, "the worktree was deleted and nothing was kept"
    kept = "\n".join(p.read_text(errors="replace") for p in patches)
    assert "new.py" in kept


def test_cleanup_keeps_nothing_for_a_worktree_that_changed_nothing(tmp_path):
    """The guard: preserving work must not litter a patch per empty tree."""
    _repo(tmp_path)
    pool = _pool(tmp_path)
    pool.acquire("t")

    pool.cleanup()

    patches = list((tmp_path / "iso" / "patches").glob("*"))
    assert not patches, f"an empty worktree left {patches}"


# --- G6: "exactly as it was" includes what a file is ---------------------


def test_a_refused_integration_does_not_delete_a_dangling_symlink(tmp_path):
    """`_snapshot` asks `is_file()`, which *follows* the link.

    A symlink pointing at something not yet generated — a build output, a
    mounted path, a sibling checkout — answers False, so the snapshot records
    it as "did not exist". The restore then honours that by unlinking it. The
    user loses a file the patch was refused for touching, from the code path
    whose docstring promises the tree is left exactly as it was.
    """
    run = _repo(tmp_path)
    (tmp_path / "link.txt").symlink_to("generated/output.txt")
    assert not (tmp_path / "link.txt").exists(), "this test needs a dangling link"
    run("git", "add", "-A")
    run("git", "commit", "-qm", "dangling symlink")

    pool = _pool(tmp_path)
    work = pool.acquire("t")
    (work / "link.txt").unlink()
    (work / "link.txt").symlink_to("generated/task.txt")

    # The user repoints the same link, so the three-way has no base to work from.
    (tmp_path / "link.txt").unlink()
    (tmp_path / "link.txt").symlink_to("generated/user.txt")

    result = pool.integrate("t")

    assert not result.applied, "this test needs the apply to fail"
    assert (tmp_path / "link.txt").is_symlink(), "the user's symlink was deleted"
    assert os.readlink(tmp_path / "link.txt") == "generated/user.txt"
    pool.cleanup()


def test_a_refused_integration_leaves_a_live_symlink_pointing_where_it_did(tmp_path):
    """The guard for the fix above, and the write-through it must not do.

    Restoring bytes to a path that is a symlink writes *through* it into the
    target — so this also asserts the file the link points at is untouched.
    """
    run = _repo(tmp_path)
    (tmp_path / "real.txt").write_text("the target's own content\n")
    (tmp_path / "other.txt").write_text("another target\n")
    (tmp_path / "link.txt").symlink_to("real.txt")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "live symlink")

    pool = _pool(tmp_path)
    work = pool.acquire("t")
    (work / "link.txt").unlink()
    (work / "link.txt").write_text("the task replaced the link\n")

    (tmp_path / "link.txt").unlink()
    (tmp_path / "link.txt").symlink_to("other.txt")

    result = pool.integrate("t")

    assert not result.applied, "this test needs the apply to fail"
    assert (tmp_path / "link.txt").is_symlink()
    assert os.readlink(tmp_path / "link.txt") == "other.txt"
    assert (tmp_path / "real.txt").read_text() == "the target's own content\n", (
        "the restore wrote through a symlink into the file it points at"
    )
    pool.cleanup()
