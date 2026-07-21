"""Workflow/enforcer configuration for vise.

Slimmed port of jig's ``hub_config``: only the pieces the graph
subsystem needs. XDG-only. ``hub_dir`` points at ``~/.local/share/vise``,
``workflows_dir`` holds YAML graph definitions, per-project state lives
under ``~/.local/share/vise/states/<project>/``. Everything resolves off
``vise.core.paths.data_dir()``. No MCP/proxy configuration lives here.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from vise.core import paths
from vise.core import state_paths as _state_paths


def get_hub_dir() -> Path:
    """Root data directory: ~/.local/share/vise (XDG-aware)."""
    return paths.data_dir()


def get_global_workflows_dir() -> Path:
    """Global (user-scope) workflows library: ~/.local/share/vise/workflows."""
    return paths.data_dir() / "workflows"


def get_project_state_dir(project_dir: str) -> Path:
    """Per-project state directory (canonical: vise.core.state_paths)."""
    return _state_paths.state_dir(project_dir)


def get_workflows_library_dir(project_dir: str | None = None) -> Path:
    """Global workflows library (shared across projects)."""
    return get_global_workflows_dir()


# ============================================================================
# Enforcer Configuration
# ============================================================================


def get_enforcer_config_file(project_dir: str) -> Path:
    return get_project_state_dir(project_dir) / "config.json"


def load_enforcer_config(project_dir: str) -> dict:
    config_file = get_enforcer_config_file(project_dir)
    if config_file.exists():
        try:
            return json.loads(config_file.read_text())
        except Exception as e:
            print(f"[vise] warning: failed to load enforcer config: {e}", file=sys.stderr)
    return {"enforcer_enabled": True, "mid_phase_dcc": True}


def save_enforcer_config(project_dir: str, config: dict) -> None:
    config_file = get_enforcer_config_file(project_dir)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config["last_updated"] = datetime.now().isoformat()
    config_file.write_text(json.dumps(config, indent=2))
