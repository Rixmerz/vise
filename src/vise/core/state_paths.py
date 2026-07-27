"""Canonical state-path resolution for vise.

Single source of truth for XDG-based per-project state directory layout.
All engines and tools that need to locate ``~/.local/share/vise/states/<project>/``
must go through this module.

Layout produced:
    ~/.local/share/vise/states/<project_slug>/                 ← state_dir()
    ~/.local/share/vise/states/<project_slug>/graph_state.json ← graph_state_path()

``state_dir``/``probe_state_dir`` resolve to the plain ``<basename>``
directory unless a DIFFERENT project already claimed that basename (see
``vise.hooks._xdg.project_state_dir``/``claim_project_state_dir``), in
which case a collision-proof ``<basename>-<hash>`` sibling is used
instead. This keeps the common case (one project per basename, which is
what other tools such as the hard-blocking ``graph_enforcer.py`` hook
hardcode) identical to the legacy layout — zero migration needed — while
still resolving real collisions between two different project
directories that happen to share a basename.

All paths respect ``$XDG_DATA_HOME`` (via ``vise.core.paths.data_dir()``,
which itself delegates to ``vise.hooks._xdg.data_dir()`` — the single
stdlib-only source of truth also used directly by the hard-blocking hooks
deployed into ``.claude/hooks/``, which cannot import this package).
"""
from __future__ import annotations

from pathlib import Path

from vise.hooks import _xdg


def project_slug(project_dir: str | Path) -> str:
    """Return the current directory name used for *project_dir*'s state.

    ``<basename>`` in the common case, or the collision-proof
    ``<basename>-<8 hex chars>`` (see ``vise.hooks._xdg.project_key``) if a
    different project already claimed that basename.
    """
    return _xdg.project_state_dir(project_dir).name


def state_dir(project_dir: str | Path) -> Path:
    """Persistent state directory for *project_dir*.

    Creates the directory (parents included) on first call and claims it
    (see ``vise.hooks._xdg.claim_project_state_dir``) so a later, different
    project sharing the same basename is diverted to its own directory
    instead of silently sharing this one.
    Canonical: ``$XDG_DATA_HOME/vise/states/<basename>[-<hash>]/``
    """
    d = _xdg.project_state_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    _xdg.claim_project_state_dir(project_dir, d)
    return d


def probe_state_dir(project_dir: str | Path) -> Path | None:
    """Return the state directory if it already exists, else None.

    Does NOT create the directory. Intended for read-only hooks. Note:
    resolving a legacy directory here still performs the same one-time
    migration rename as ``state_dir`` (it relocates existing state onto
    the new key rather than creating anything new).
    """
    d = _xdg.project_state_dir(project_dir)
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
