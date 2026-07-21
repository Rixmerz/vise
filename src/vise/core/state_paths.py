"""Canonical state-path resolution for vise.

Single source of truth for XDG-based per-project state directory layout.
All engines and tools that need to locate ``~/.local/share/vise/states/<project>/``
must go through this module.

Layout produced:
    ~/.local/share/vise/states/<project_slug>/                 ← state_dir()
    ~/.local/share/vise/states/<project_slug>/graph_state.json ← graph_state_path()

All paths respect ``$XDG_DATA_HOME`` (via ``vise.core.paths.data_dir()``).

Note: hard-blocking hooks deployed into ``.claude/hooks/`` keep their own
stdlib-only copy of this path logic; keep the two in sync when the XDG
layout changes.
"""
from __future__ import annotations

from pathlib import Path

from vise.core import paths as _paths


def project_slug(project_dir: str | Path) -> str:
    """Return the canonical project identifier: the basename of *project_dir*.

    >>> project_slug("/home/user/projects/my-app")
    'my-app'
    """
    return Path(project_dir).name


def state_dir(project_dir: str | Path) -> Path:
    """Persistent state directory for *project_dir*.

    Creates the directory (parents included) on first call.
    Canonical: ``$XDG_DATA_HOME/vise/states/<basename>/``
    """
    d = _paths.data_dir() / "states" / project_slug(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def probe_state_dir(project_dir: str | Path) -> Path | None:
    """Return the state directory if it already exists, else None.

    Does NOT create the directory. Intended for read-only hooks.
    """
    d = _paths.data_dir() / "states" / project_slug(project_dir)
    return d if d.exists() else None


def graph_state_path(project_dir: str | Path) -> Path:
    """Path to the graph execution state blob for *project_dir*.

    Canonical: ``$XDG_DATA_HOME/vise/states/<basename>/graph_state.json``
    """
    return state_dir(project_dir) / "graph_state.json"


__all__ = [
    "graph_state_path",
    "probe_state_dir",
    "project_slug",
    "state_dir",
]
