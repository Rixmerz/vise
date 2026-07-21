"""graph_enforcer_toggle — in-band on/off switch for the PreToolUse
graph enforcer hook.

The PreToolUse hook (``graph_enforcer.py``) blocks tools listed in the
active node's ``tools_blocked``. That's the contract that makes phase
gates work. But sometimes you need to step outside the gate without
abandoning the workflow — to run a one-off diagnostic, ship a hotfix,
or unstick yourself when the gate's intent and the reality have
diverged.

This tool flips the ``enforcer_enabled`` flag in the per-project config
that the hook reads on every invocation. While ``enabled=False``, the
hook short-circuits to ``approve`` regardless of the active graph.
While ``enabled=True`` (the default), normal phase gating applies.

The tool itself is in the hook's hardcoded allowlist
(``ENFORCER_ALLOWLIST`` in ``graph_enforcer.py``), so even if the
active workflow blocks every tool with ``tools_blocked: ['*']``, this
toggle still goes through. Recovery is always one call away.
"""
from __future__ import annotations

import json
from pathlib import Path

from vise.core.session import resolve_project_dir
from vise.engines.graph_state import _get_centralized_state_dir


def _config_path(project_dir: str) -> Path:
    return _get_centralized_state_dir(project_dir) / "config.json"


def _read_config(project_dir: str) -> dict:
    path = _config_path(project_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_config(project_dir: str, cfg: dict) -> Path:
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))
    return path


def register_graph_enforcer_control_tools(mcp) -> None:

    @mcp.tool()
    def graph_enforcer_toggle(
        enabled: bool,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Turn the PreToolUse graph enforcer on or off for this project.

        While disabled, the enforcer hook approves every tool regardless
        of the active workflow's ``tools_blocked`` list. The active
        graph state is **not** cleared — when you re-enable, gating
        resumes from whatever node was active.

        Use this when:
          - A phase's tools_blocked is overly restrictive for the
            specific operation you need (e.g. a single Read in an
            "implement" phase that blocks Read).
          - You need to investigate or hotfix without aborting the
            workflow.
          - The user explicitly asks to suspend gating temporarily.

        Prefer ``graph_traverse`` (advance the phase) when the workflow
        intent matches the next phase. Prefer ``graph_reset`` when
        you're abandoning the workflow entirely. ``graph_enforcer_toggle``
        is the right tool only when you want to *pause* gating without
        moving phases or losing state.

        This tool is in the enforcer's hardcoded allowlist, so it can
        always be called — even when ``tools_blocked: ['*']`` is in
        effect. That's intentional: it's the in-band recovery path.

        Args:
            enabled: ``True`` to gate normally (default), ``False`` to
                disable gating.
            project_dir: Project directory (optional after set_session).
            session_id: Optional session id.
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        cfg = _read_config(resolved_dir)
        previous = cfg.get("enforcer_enabled", True)
        cfg["enforcer_enabled"] = bool(enabled)
        path = _write_config(resolved_dir, cfg)
        return {
            "success": True,
            "enforcer_enabled": bool(enabled),
            "previous": bool(previous),
            "config_path": str(path),
            "project_dir": resolved_dir,
            "note": (
                "Active graph state is preserved. Re-enable to resume "
                "phase gating from the same node."
            ),
        }

    @mcp.tool()
    def graph_enforcer_status(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Read whether the PreToolUse graph enforcer is currently
        gating tools for this project. Read-only.
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        cfg = _read_config(resolved_dir)
        enabled = cfg.get("enforcer_enabled", True)
        return {
            "enforcer_enabled": bool(enabled),
            "default_when_unset": True,
            "config_path": str(_config_path(resolved_dir)),
            "project_dir": resolved_dir,
        }
