"""Snapshot MCP tools — exposed directly (no proxy archive)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastmcp import FastMCP

from vise.core import snapshots
from vise.core.session import get_session_project_dir


def register_snapshot(mcp: "FastMCP") -> None:
    @mcp.tool()
    def snapshot_create(
        label: str = "",
        phase: str = "",
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Capture the current working tree to refs/vise/snapshots/<id>.

        Does not pollute git tags or branches. Label/phase are stored for audit.
        """
        project = _resolve(project_dir)
        snap = snapshots.create(project, label=label, phase=phase)
        if snap is None:
            return {"error": "not a git repository"}
        return {
            "id": snap.id,
            "ref": snap.ref,
            "commit": snap.commit,
            "label": snap.label,
            "phase": snap.phase,
        }

    @mcp.tool()
    def snapshot_list(project_dir: str | None = None) -> dict[str, Any]:
        """List snapshots in reverse chronological order."""
        project = _resolve(project_dir)
        snaps = snapshots.list_all(project)
        snaps.sort(key=lambda s: s.created_at, reverse=True)
        return {
            "snapshots": [
                {
                    "id": s.id,
                    "ref": s.ref,
                    "label": s.label,
                    "phase": s.phase,
                    "created_at": s.created_at,
                }
                for s in snaps
            ]
        }

    @mcp.tool()
    def snapshot_diff(a: str, b: str, project_dir: str | None = None) -> dict[str, Any]:
        """Show `git diff <a>..<b>` between two snapshot ids or refs."""
        project = _resolve(project_dir)
        try:
            text = snapshots.diff(project, a, b)
        except RuntimeError as e:
            return {"error": str(e)}
        return {"diff": text}

    @mcp.tool()
    def snapshot_restore(
        snapshot_id: str,
        dry_run: bool = True,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Preview or apply a restore from a snapshot.

        When dry_run=True (default), returns the diff that would be applied.
        Pass dry_run=False to actually overwrite the working tree. This does
        not auto-commit — the user decides whether to stage/commit the result.
        """
        project = _resolve(project_dir)
        try:
            out = snapshots.restore(project, snapshot_id, dry_run=dry_run)
        except RuntimeError as e:
            return {"error": str(e)}
        return {"result": out, "dry_run": dry_run}


def _resolve(project_dir: str | None) -> Path:
    if project_dir:
        return Path(project_dir).expanduser().resolve()
    sess = get_session_project_dir(None)
    if sess:
        return Path(sess).expanduser().resolve()
    return Path.cwd()


__all__ = ["register_snapshot"]
