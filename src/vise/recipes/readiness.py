"""L3 recipe readiness gate — five-point pre-run check for unattended execution.

All five points must pass before an L3 recipe is allowed to run.  A failure
returns a ``not-ready`` result listing the specific checks that failed; the
runner refuses to execute and returns the failure detail.

The five checks (per the loop-mode-design spec):

  (a) All step capabilities resolved/bound — no unresolved gaps would cause a
      mid-run halt before any side-effecting step fires.
  (b) VISE_GOAL_GATE=1 — the autonomy rails are armed; the Stop hook will catch
      plateau / max-attempts / fabricated completions.
  (c) Clean git working tree — ``git status --porcelain`` returns empty, so an
      unattended write cannot collide with uncommitted WIP.
  (d) Estimated cost within cap — ``estimate_cost`` total ≤ ``VISE_LOOP_COST_CAP``
      (default 50 000 tokens).
  (e) A mechanical done-validator is present — at least one step uses
      ``meta.assert`` (the built-in assertion step) to prevent fabricated
      completion signals.

Design: purely synchronous, no I/O heavier than a single subprocess call for
the git porcelain check.  Does not touch the database or any MCP proxy.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _check_capabilities_resolved(recipe: Any, project_dir: str) -> str | None:
    """(a) Return failure message if any step capability is unresolved."""
    try:
        from vise.recipes.loader import load_capabilities, load_user_pins
        from vise.recipes.resolver import resolve_capability

        assignments = load_capabilities(project_dir)
        user_pins = load_user_pins(project_dir)
        unresolved = [
            step.capability
            for step in recipe.steps
            if resolve_capability(step.capability, assignments, user_pins) is None
        ]
        if unresolved:
            return f"unresolved capabilities: {', '.join(sorted(unresolved))}"
    except Exception as e:
        return f"capability resolution check failed: {e}"
    return None


def _check_goal_gate_enabled() -> str | None:
    """(b) Return failure message if VISE_GOAL_GATE is not '1'."""
    from vise.engines.goal_gate import is_gate_enabled

    if not is_gate_enabled():
        return "VISE_GOAL_GATE is not set to 1 — autonomy rails are disarmed"
    return None


def _check_clean_worktree(project_dir: str) -> str | None:
    """(c) Return failure message if the git working tree has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            # Not a git repo or git not available — fail-open: don't block L3
            # for non-git projects.  Log a debug note and skip this check.
            log.debug(
                "[readiness] git status returned %d — skipping worktree check",
                result.returncode,
            )
            return None
        dirty = result.stdout.strip()
        if dirty:
            lines = dirty.splitlines()
            preview = lines[0] if lines else "(unknown)"
            count = len(lines)
            return (
                f"git working tree is not clean ({count} change(s), e.g. {preview!r}) — "
                "commit or stash WIP before running L3 unattended"
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        # git not on PATH or timed out — fail-open
        log.debug("[readiness] git status unavailable: %s — skipping worktree check", e)
    return None


def _check_cost_within_cap(recipe: Any, project_dir: str) -> str | None:
    """(d) Return failure message if estimated cost exceeds VISE_LOOP_COST_CAP."""
    try:
        from vise.recipes.cost import estimate_cost

        cost_info = estimate_cost(recipe)
        if not cost_info["within_cap"]:
            return (
                f"estimated cost {cost_info['est_total_tokens']} tokens exceeds cap "
                f"{cost_info['cap']} (VISE_LOOP_COST_CAP) — "
                "set a higher cap or reduce recipe scope"
            )
    except Exception as e:
        return f"cost estimation failed: {e}"
    return None


def _check_mechanical_validator(recipe: Any) -> str | None:
    """(e) Return failure message if no meta.assert step is present."""
    has_assert = any(step.capability == "meta.assert" for step in recipe.steps)
    if not has_assert:
        return (
            "no mechanical done-validator (meta.assert step) declared — "
            "add a meta.assert step so completion cannot be fabricated"
        )
    return None


# ---------------------------------------------------------------------------
# Main readiness check
# ---------------------------------------------------------------------------

def check_readiness(
    recipe: Any,
    project_dir: str | Path,
) -> dict[str, Any]:
    """Run all five L3 readiness checks for *recipe*.

    Args:
        recipe: A ``Recipe`` dataclass instance.
        project_dir: Absolute path to the project root.

    Returns::

        {
            "ready": <bool>,
            "failed_checks": ["<check-label>: <reason>", ...],  # empty when ready
            "checks": {
                "capabilities_resolved": True|False,
                "goal_gate_enabled":     True|False,
                "worktree_clean":        True|False,
                "cost_within_cap":       True|False,
                "has_mechanical_validator": True|False,
            }
        }
    """
    project_dir_str = str(project_dir)

    a_err = _check_capabilities_resolved(recipe, project_dir_str)
    b_err = _check_goal_gate_enabled()
    c_err = _check_clean_worktree(project_dir_str)
    d_err = _check_cost_within_cap(recipe, project_dir_str)
    e_err = _check_mechanical_validator(recipe)

    failed: list[str] = []
    if a_err:
        failed.append(f"capabilities_resolved: {a_err}")
    if b_err:
        failed.append(f"goal_gate_enabled: {b_err}")
    if c_err:
        failed.append(f"worktree_clean: {c_err}")
    if d_err:
        failed.append(f"cost_within_cap: {d_err}")
    if e_err:
        failed.append(f"has_mechanical_validator: {e_err}")

    return {
        "ready": len(failed) == 0,
        "failed_checks": failed,
        "checks": {
            "capabilities_resolved": a_err is None,
            "goal_gate_enabled": b_err is None,
            "worktree_clean": c_err is None,
            "cost_within_cap": d_err is None,
            "has_mechanical_validator": e_err is None,
        },
    }
