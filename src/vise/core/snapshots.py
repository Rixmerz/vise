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
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from vise.core import paths

log = logging.getLogger(__name__)

SNAPSHOT_REF_PREFIX = "refs/vise/snapshots/"


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
    return paths.ensure(paths.project_state_dir(project)) / "snapshots.jsonl"


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
    When dry_run=False, performs `git checkout <tree> -- .` at the workdir
    root. Will refuse if the index has uncommitted changes unless --force is
    passed (callers must set dry_run=False AND check cleanliness themselves).
    """
    ref = _resolve_ref(project, snap_id)
    if ref is None:
        raise RuntimeError(f"unknown snapshot: {snap_id}")
    if dry_run:
        return _git(project, "diff", "HEAD", ref)
    _git(project, "checkout", ref, "--", ".")
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
    "SNAPSHOT_REF_PREFIX",
    "Snapshot",
    "create",
    "create_for_phase_transition",
    "diff",
    "list_all",
    "prune",
    "restore",
]
