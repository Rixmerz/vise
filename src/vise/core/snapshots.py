"""Shadow-branch snapshot system.

Snapshots live under `refs/vise/snapshots/<id>` as orphan commits. They do NOT
pollute `git tag -l`, `git branch -a`, or `git log` default views. The working
tree is captured via `git write-tree` (staged index) plus a synthesized tree of
the workdir via `git add -A` + reset (safe: we use a temporary index).

Snapshot record format (append-only JSONL at .vise/snapshots.jsonl):
    {"id": "20260419T120000-abc1", "ref": "refs/vise/snapshots/...", "label": "...",
     "phase": "understand", "created_at": 1713523200, "tree": "<sha>", "commit": "<sha>"}
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from vise.core import paths

log = logging.getLogger(__name__)

SNAPSHOT_REF_PREFIX = "refs/vise/snapshots/"

# Shared with hooks/snapshot_trigger.py — read the env var in exactly one
# place so the hook and the snapshot_list tool never disagree on its meaning.
ON_EDIT_ENV_VAR = "VISE_SNAPSHOT_ON_EDIT"
_TRUTHY = {"1", "true", "yes", "on"}


def on_edit_capture_enabled() -> bool:
    """Whether the opt-in per-edit snapshot hook is enabled.

    Off by default; truthy values for ``VISE_SNAPSHOT_ON_EDIT`` turn it on.
    Does not affect explicit ``snapshot_create`` calls, which always work.
    """
    return os.environ.get(ON_EDIT_ENV_VAR, "").strip().lower() in _TRUTHY


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: str
    ref: str
    commit: str
    tree: str
    label: str
    phase: str
    created_at: float


# ---------------------------------------------------------------------------
# git primitives
# ---------------------------------------------------------------------------


def _git(project: Path, *args: str, input: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        text=True,
        input=input,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.rstrip("\n")


def _is_git_repo(project: Path) -> bool:
    try:
        _git(project, "rev-parse", "--is-inside-work-tree")
        return True
    except RuntimeError:
        return False


def _snapshot_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:4]}"


def _journal_path(project: Path) -> Path:
    state_dir = paths.ensure(paths.project_state_dir(project))
    _ensure_state_dir_gitignored(Path(project))
    return state_dir / "snapshots.jsonl"


def _targets_vise_dir(line: str) -> bool:
    """Does this .gitignore line already put the whole `.vise` dir out of scope?

    Accepts every spelling of the directory — `.vise`, `.vise/`, `/.vise/`,
    `.vise/*` — because appending a redundant one is not harmless. A repo that
    tracks a file inside `.vise` writes the two-line form:

        .vise/*
        !.vise/quality.yaml

    and a `.vise/` appended underneath silently voids the negation: git never
    descends into an excluded directory, so the re-include can never match.
    vise ships exactly that pair in its own .gitignore, and the old check —
    equality against `.vise` after an rstrip("/") — did not recognize `.vise/*`,
    so taking a snapshot of vise re-broke vise's own tracked quality profile.

    A negation (`!...`) is never coverage, and normalizes to itself.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return False
    return s.removeprefix("/").removesuffix("/*").rstrip("/") == ".vise"


def _ensure_state_dir_gitignored(project: Path) -> None:
    """Ensure `.vise/` is ignored in the target project's .gitignore.

    Idempotent: appends `.vise/` only when the project has a `.git` dir and
    no existing .gitignore line already covers it. Never raises.
    """
    try:
        if not (project / ".git").is_dir():
            return
        gitignore = project / ".gitignore"
        if gitignore.exists():
            if any(
                _targets_vise_dir(line)
                for line in gitignore.read_text(encoding="utf-8").splitlines()
            ):
                return
            existing = gitignore.read_text(encoding="utf-8")
            prefix = "" if (not existing or existing.endswith("\n")) else "\n"
            with gitignore.open("a", encoding="utf-8") as fh:
                fh.write(f"{prefix}.vise/\n")
        else:
            gitignore.write_text(".vise/\n", encoding="utf-8")
    except OSError as exc:
        log.warning("[vise.snapshot] could not update .gitignore (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def create(project: Path, *, label: str = "", phase: str = "") -> Snapshot | None:
    """Create a snapshot of the current working tree (tracked + untracked).

    Returns None if the directory isn't a git repo. Never modifies user-visible
    refs: branches, tags, HEAD, and working index are all untouched.
    """
    if not _is_git_repo(project):
        return None

    # Use a temporary index so we don't disturb the user's staged changes.
    tmp_index = paths.project_state_dir(project) / "tmp.index"
    paths.ensure(tmp_index.parent)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(tmp_index)

    # Before staging, not after. `.gitignore` used to be written lazily by
    # `_journal_path`, which runs *after* `write-tree`, so on the very first
    # snapshot in a repo the ignore line did not exist yet and `add -A` swept
    # `.vise/` into the tree — including `tmp.index.lock`, the lock git itself
    # was holding one directory down at that exact moment. That file then sat
    # in the snapshot forever, and `restore` wrote it back out.
    #
    # An `:(exclude).vise` pathspec would be the more direct expression, but
    # `git add` exits 1 whenever a pathspec names an ignored path, so it
    # cannot be combined with the ignore rule it is meant to complement.
    _ensure_state_dir_gitignored(project)

    try:
        # Stage everything (tracked + untracked) in the temp index
        subprocess.run(
            ["git", "-C", str(project), "add", "-A"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        tree = subprocess.run(
            ["git", "-C", str(project), "write-tree"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Orphan commit (no parent), metadata in message body
        parent = _resolve_head(project)
        msg_body = {
            "label": label,
            "phase": phase,
            "source_commit": parent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        commit_msg = f"vise snapshot {label or '(unlabeled)'}\n\n{json.dumps(msg_body, indent=2)}"
        commit_args = ["git", "-C", str(project), "commit-tree", tree, "-m", commit_msg]
        if parent:
            commit_args += ["-p", parent]
        commit = subprocess.run(
            commit_args,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        sid = _snapshot_id()
        ref = f"{SNAPSHOT_REF_PREFIX}{sid}"
        subprocess.run(
            ["git", "-C", str(project), "update-ref", ref, commit],
            check=True,
            capture_output=True,
            text=True,
        )
        snap = Snapshot(
            id=sid,
            ref=ref,
            commit=commit,
            tree=tree,
            label=label,
            phase=phase,
            created_at=time.time(),
        )
        _append_journal(project, snap)
        return snap
    finally:
        with contextlib.suppress(OSError):
            tmp_index.unlink(missing_ok=True)


def _resolve_head(project: Path) -> str | None:
    try:
        return _git(project, "rev-parse", "HEAD")
    except RuntimeError:
        return None  # empty repo


def _append_journal(project: Path, snap: Snapshot) -> None:
    path = _journal_path(project)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "id": snap.id,
            "ref": snap.ref,
            "commit": snap.commit,
            "tree": snap.tree,
            "label": snap.label,
            "phase": snap.phase,
            "created_at": snap.created_at,
        }) + "\n")


def list_all(project: Path) -> list[Snapshot]:
    """List snapshots from the journal (authoritative local index)."""
    path = _journal_path(project)
    if not path.exists():
        return []
    snaps: list[Snapshot] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                snaps.append(Snapshot(
                    id=d["id"],
                    ref=d["ref"],
                    commit=d["commit"],
                    tree=d.get("tree", ""),
                    label=d.get("label", ""),
                    phase=d.get("phase", ""),
                    created_at=d.get("created_at", 0.0),
                ))
            except (json.JSONDecodeError, KeyError):
                continue
    return snaps


def diff(project: Path, a: str, b: str) -> str:
    """Show `git diff` between two snapshot ids (or refs)."""
    ra = _resolve_ref(project, a)
    rb = _resolve_ref(project, b)
    if ra is None or rb is None:
        raise RuntimeError(f"could not resolve refs: {a}={ra}, {b}={rb}")
    return _git(project, "diff", ra, rb)


def restore(project: Path, snap_id: str, *, dry_run: bool = True) -> str:
    """Preview (or perform) a restore from the given snapshot.

    When dry_run=True (default), returns the diff that would be applied.
    When dry_run=False, rewrites the working tree from the snapshot and
    leaves the user's index exactly as it was — the caller decides what to
    stage. A plain `git checkout <ref> -- .` does not do that: it writes the
    index too, so every restored file arrived silently staged, and the next
    `git commit` the user typed would have swept the whole rollback in
    alongside whatever they had deliberately staged. Redirecting the write
    through a throwaway copy of the index keeps the worktree effect and drops
    the staging side effect.

    Restore is additive: files created after the snapshot are left alone.
    Rolling back is not the same as discarding work the snapshot never saw.
    """
    ref = _resolve_ref(project, snap_id)
    if ref is None:
        raise RuntimeError(f"unknown snapshot: {snap_id}")
    if dry_run:
        return _git(project, "diff", "HEAD", ref)

    real_index = Path(_git(project, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    tmp_index = paths.ensure(paths.project_state_dir(project)) / "restore.index"
    try:
        if real_index.exists():
            shutil.copyfile(real_index, tmp_index)
        elif tmp_index.exists():
            tmp_index.unlink()
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(tmp_index)
        result = subprocess.run(
            ["git", "-C", str(project), "checkout", ref, "--", "."],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"restore failed: {result.stderr.strip()}")
    finally:
        with contextlib.suppress(OSError):
            tmp_index.unlink(missing_ok=True)
    return f"restored workdir from {ref}"


def prune(project: Path, keep: int = 100) -> int:
    """Keep only the most recent `keep` snapshots. Returns count deleted."""
    snaps = list_all(project)
    if len(snaps) <= keep:
        return 0
    to_delete = sorted(snaps, key=lambda s: s.created_at)[: len(snaps) - keep]
    for snap in to_delete:
        with contextlib.suppress(RuntimeError):
            _git(project, "update-ref", "-d", snap.ref)
    # Rewrite journal keeping only survivors
    survivors = sorted(snaps, key=lambda s: s.created_at)[-keep:]
    path = _journal_path(project)
    with path.open("w", encoding="utf-8") as fh:
        for snap in survivors:
            fh.write(json.dumps({
                "id": snap.id, "ref": snap.ref, "commit": snap.commit,
                "tree": snap.tree, "label": snap.label, "phase": snap.phase,
                "created_at": snap.created_at,
            }) + "\n")
    return len(to_delete)


def _resolve_ref(project: Path, handle: str) -> str | None:
    if handle.startswith(SNAPSHOT_REF_PREFIX):
        return handle
    candidate = f"{SNAPSHOT_REF_PREFIX}{handle}"
    try:
        _git(project, "rev-parse", candidate)
        return candidate
    except RuntimeError:
        try:
            _git(project, "rev-parse", handle)
            return handle
        except RuntimeError:
            return None


def create_for_phase_transition(
    project: Path,
    *,
    workflow_name: str,
    from_node: str,
    to_node: str,
) -> Snapshot | None:
    """Create a snapshot on a graph_traverse phase transition.

    Bypasses the 30 s edit-triggered throttle — phase transitions are
    discrete events, not edit spam.  The label embeds the workflow name
    and the ``from_node ->>> to_node`` transition tag so
    ``git log refs/vise/snapshots/...`` shows phase checkpoints clearly.

    Returns None (and does NOT raise) if the directory is not a git repo
    or if snapshot creation fails — callers must not be blocked.
    """
    label = f"phase={from_node}--->{to_node} workflow={workflow_name}"
    phase_tag = f"phase={from_node}--->{to_node}"
    try:
        return create(project, label=label, phase=phase_tag)
    except Exception as exc:
        log.warning("[vise.snapshot] phase-transition snapshot failed (non-fatal): %s", exc)
        return None


__all__ = [
    "ON_EDIT_ENV_VAR",
    "SNAPSHOT_REF_PREFIX",
    "Snapshot",
    "create",
    "create_for_phase_transition",
    "diff",
    "list_all",
    "on_edit_capture_enabled",
    "prune",
    "restore",
]
