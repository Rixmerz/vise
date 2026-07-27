"""XDG-compliant path resolution for vise state, config, cache, and data.

Directory layout:
    ~/.config/vise/         → user configuration
    ~/.local/share/vise/    → persistent application data (embeddings, snapshots)
    ~/.cache/vise/          → regenerable caches
    $PROJECT/.vise/         → per-project state (lockfiles, ephemeral)

All paths respect $XDG_CONFIG_HOME / $XDG_DATA_HOME / $XDG_CACHE_HOME when set.

``data_dir()`` delegates to ``vise.hooks._xdg.data_dir()`` — that stdlib-only
module is the single source of truth for the data-dir computation, shared
with the hard-blocking hooks (which cannot import this heavier package).
"""
from __future__ import annotations

import os
from pathlib import Path

from vise.hooks import _xdg

_APP = "vise"


def _xdg_env(var: str, default: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser() if raw else default


def config_dir() -> Path:
    """User configuration: ~/.config/vise/ by default."""
    base = _xdg_env("XDG_CONFIG_HOME", Path.home() / ".config")
    return base / _APP


def data_dir() -> Path:
    """Persistent application data: ~/.local/share/vise/ by default."""
    return _xdg.data_dir()


def cache_dir() -> Path:
    """Regenerable cache: ~/.cache/vise/ by default."""
    base = _xdg_env("XDG_CACHE_HOME", Path.home() / ".cache")
    return base / _APP


def project_state_dir(project_dir: Path) -> Path:
    """Per-project ephemeral state: $PROJECT/.vise/."""
    return Path(project_dir) / ".vise"


def ensure(path: Path) -> Path:
    """Create the directory (parents included) and return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "cache_dir",
    "config_dir",
    "data_dir",
    "ensure",
    "project_state_dir",
]
