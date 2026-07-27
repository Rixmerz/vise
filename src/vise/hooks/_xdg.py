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

import hashlib
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


_ORIGIN_MARKER = ".vise-project-origin"


def project_key(project_dir: str | Path) -> str:
    """Collision-proof per-project key: ``<basename>-<8 hex chars>``.

    The suffix is a stable hash of the resolved absolute path, so two
    different directories sharing a basename (e.g. ``~/Projects/mcp`` and
    ``~/mcp``) never collide onto the same state key. Not a security
    hash — just a namespacing suffix. Only used as the escape hatch when
    ``project_state_dir`` detects an actual basename collision.
    """
    resolved = Path(project_dir).resolve()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{resolved.name}-{digest}"


def project_state_dir(project_dir: str | Path) -> Path:
    """``<data_dir>/states/<basename>`` — or a hashed sibling on collision.

    Read-only resolution (no filesystem writes): the plain ``<basename>``
    directory is used unless an ``_ORIGIN_MARKER`` file inside it names a
    *different* resolved absolute path, in which case a collision-proof
    ``<basename>-<hash>`` directory (see ``project_key``) is used instead.

    This keeps the common case (one project per basename — the vast
    majority) byte-identical to the legacy path, so pre-existing state is
    found with zero migration. Every caller, including the hard-blocking
    ``graph_enforcer.py`` hook, resolves through here rather than
    rebuilding the path, so there is exactly one answer. Claiming
    the plain directory (writing the marker) is a separate, explicit step
    — see ``claim_project_state_dir`` — so this function stays safe to
    call from read-only probes.
    """
    base = states_dir()
    plain_dir = base / Path(project_dir).name
    marker = plain_dir / _ORIGIN_MARKER
    if marker.exists():
        try:
            owner = marker.read_text(encoding="utf-8").strip()
        except OSError:
            owner = None
        if owner is not None and owner != str(Path(project_dir).resolve()):
            return base / project_key(project_dir)
    return plain_dir


def claim_project_state_dir(project_dir: str | Path, resolved_dir: Path) -> None:
    """Record *project_dir*'s absolute path as the owner of *resolved_dir*.

    Called by the mutating entry point (``vise.core.state_paths.state_dir``)
    right after creating the directory, so a later basename collision from
    a *different* project can be detected by ``project_state_dir`` above.
    No-op (best-effort) if the marker can't be written or already matches.
    """
    marker = resolved_dir / _ORIGIN_MARKER
    origin = str(Path(project_dir).resolve())
    try:
        if not marker.exists() or marker.read_text(encoding="utf-8").strip() != origin:
            marker.write_text(origin, encoding="utf-8")
    except OSError:
        pass


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
