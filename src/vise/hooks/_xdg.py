"""Single source of truth for vise's XDG data directory (stdlib-only).

Hard-blocking hooks run standalone (no guaranteed ``vise`` package on
``sys.path``) and must never fail, so this module imports nothing but
``os`` and ``pathlib``. ``vise.core.paths.data_dir()`` delegates here so
there is exactly ONE place that computes the path — see that module's
docstring.

Per the XDG Base Directory spec, a relative ``$XDG_DATA_HOME`` is invalid
and must be ignored (fall back to the default).
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Base vise data directory: ``$XDG_DATA_HOME/vise`` or ``~/.local/share/vise``."""
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw) if raw and Path(raw).is_absolute() else Path.home() / ".local" / "share"
    return base / "vise"


def states_dir() -> Path:
    """``<data_dir>/states`` — per-project graph/workflow state."""
    return data_dir() / "states"


def project_state_dir(project_dir: str | Path) -> Path:
    """``<data_dir>/states/<project_basename>``."""
    return states_dir() / Path(project_dir).name


def graph_state_path(project_dir: str | Path) -> Path:
    """``<data_dir>/states/<project_basename>/graph_state.json``."""
    return project_state_dir(project_dir) / "graph_state.json"


def experience_memory_path() -> Path:
    """Global experience memory store: ``<data_dir>/experience_memory.json``."""
    return data_dir() / "experience_memory.json"


def project_memory_path(project_name: str) -> Path:
    """Per-project experience memory store."""
    return data_dir() / "project_memories" / project_name / "experience_memory.json"


def experience_index_dir() -> Path:
    """Sidecar experience index directory."""
    return data_dir() / "experience_index"


def config_path() -> Path:
    """Legacy hub-dir override config: ``<data_dir>/config.json``."""
    return data_dir() / "config.json"


def vise_project_path() -> Path:
    return data_dir() / "vise-project.json"


def telemetry_path() -> Path:
    """Orchestration telemetry log; honors ``$VISE_TELEMETRY_DIR`` override."""
    override = os.environ.get("VISE_TELEMETRY_DIR")
    base = Path(override) if override else data_dir() / "telemetry"
    return base / "orchestration.jsonl"


def usage_dir() -> Path:
    """Usage-state directory; honors ``$VISE_USAGE_DIR`` override."""
    override = os.environ.get("VISE_USAGE_DIR")
    return Path(override) if override else data_dir() / "usage"


def goal_dir() -> Path:
    """Goal-state directory; honors ``$VISE_GOAL_DIR`` override."""
    override = os.environ.get("VISE_GOAL_DIR")
    return Path(override) if override else data_dir() / "goal"


def src_dir() -> Path:
    """Legacy in-place source checkout (pre-package-install layout)."""
    return data_dir() / "src"


__all__ = [
    "config_path",
    "data_dir",
    "experience_index_dir",
    "experience_memory_path",
    "goal_dir",
    "graph_state_path",
    "project_memory_path",
    "project_state_dir",
    "src_dir",
    "states_dir",
    "telemetry_path",
    "usage_dir",
    "vise_project_path",
]
