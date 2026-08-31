"""Two ways the snapshot layer misreads the repository it is protecting.
**The preview lies.** `snapshot_restore(dry_run=True)` promised "the diff that
would be applied" and returned `git diff HEAD <ref>`. The apply writes the
*working tree* (`git checkout <ref> -- .`), so the honest preview is
worktree-vs-ref. Anyone whose working tree was dirty — which is everyone about
to roll something back — was shown a diff that did not describe what was about
to happen to their files.
**The ignore guard misses a worktree.** `_ensure_state_dir_gitignored` bailed
unless `.git` was a directory. In a `git worktree add` checkout and in every
submodule `.git` is a regular *file* holding `gitdir: ...`, so the rule was
never written, `git add -A` swept `.vise/` into the snapshot, and restoring that
snapshot wrote vise's own state directory back over itself.
"""
from __future__ import annotations
import subprocess
from vise.core import snapshots
def _repo(tmp_path):
    def run(*args, cwd=tmp_path):
        subprocess.run(args, cwd=cwd, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (tmp_path / "app.py").write_text("original\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    return run
# --- the preview ----------------------------------------------------------
def test_the_dry_run_names_the_file_the_apply_will_overwrite(tmp_path):
    _repo(tmp_path)
    snap = snapshots.create(tmp_path, label="before")
    (tmp_path / "app.py").write_text("edited by hand\n", encoding="utf-8")
    preview = snapshots.restore(tmp_path, snap.id, dry_run=True)
    assert "app.py" in preview, (
        "the file the restore is about to overwrite is missing from its preview"
    )
    assert "edited by hand" in preview
def test_a_dry_run_on_an_unchanged_tree_previews_nothing(tmp_path):
    """The other direction: no change must read as no change."""
    _repo(tmp_path)
    snap = snapshots.create(tmp_path, label="before")
    assert snapshots.restore(tmp_path, snap.id, dry_run=True).strip() == ""
def test_the_dry_run_still_touches_neither_disk_nor_index(tmp_path):
    _repo(tmp_path)
    snap = snapshots.create(tmp_path, label="before")
    (tmp_path / "app.py").write_text("edited by hand\n", encoding="utf-8")
    snapshots.restore(tmp_path, snap.id, dry_run=True)
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "edited by hand\n"
# --- the worktree ---------------------------------------------------------
def _worktree(tmp_path):
    run = _repo(tmp_path)
    linked = tmp_path.parent / f"{tmp_path.name}-wt"
    run("git", "worktree", "add", "-q", str(linked), "-b", "side")
    assert (linked / ".git").is_file(), "a linked worktree keeps .git as a file"
    return linked
def test_a_snapshot_taken_in_a_worktree_excludes_vise_state(tmp_path):
    linked = _worktree(tmp_path)
    snap = snapshots.create(linked, label="inside a worktree")
    listed = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", snap.ref),
        cwd=linked, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert not [p for p in listed if p.startswith(".vise/")], (
        f"vise's own state was captured into the snapshot: {listed}"
    )
def test_the_ignore_rule_is_written_in_a_worktree(tmp_path):
    linked = _worktree(tmp_path)
    snapshots.create(linked, label="inside a worktree")
    gitignore = linked / ".gitignore"
    assert gitignore.exists() and ".vise/" in gitignore.read_text(encoding="utf-8")
def test_a_restore_in_a_worktree_does_not_brick_the_next_snapshot(tmp_path):
    linked = _worktree(tmp_path)
    first = snapshots.create(linked, label="one")
    (linked / "app.py").write_text("changed\n", encoding="utf-8")
    snapshots.restore(linked, first.id, dry_run=False)
    second = snapshots.create(linked, label="two")
    assert second.ref, "snapshot_create stopped working after one restore"
