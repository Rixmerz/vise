"""Recipe runner — executes a Recipe step by step.

Responsibilities:
- Render step args via the template renderer
- Resolve each capability to (mcp_name, tool_name)
- Dispatch to execute_mcp_tool or built-in handlers
- Bind step outputs for downstream {{ steps.ID.output.K }} references
- Record telemetry via trend_tracker.record_snapshot
- Redact env refs before storing telemetry
- Write per-step JSONL telemetry and enforce token budget caps
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vise.recipes.builtin import meta_assert
from vise.recipes.loader import Recipe, load_capabilities, load_user_pins
from vise.recipes.renderer import render_value
from vise.recipes.resolver import resolve_capability
from vise.recipes.telemetry import (
    BudgetTracker,
    StepTelemetryWriter,
    count_tokens,
    redact_for_telemetry,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telemetry helper
# ---------------------------------------------------------------------------

def _record_telemetry(project_dir: str, recipe_name: str, key: str, value: Any) -> None:
    """Best-effort telemetry — never raises."""
    try:
        from vise.engines.graph_state import _get_centralized_state_dir
        from vise.engines.trend_tracker import record_snapshot
        state_dir = str(_get_centralized_state_dir(project_dir))
        record_snapshot(project_dir, state_dir, {f"recipes.{recipe_name}.{key}": value})
    except Exception as e:
        log.debug("[recipes] telemetry error: %s", e)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_recipe(
    recipe: Recipe,
    inputs: dict[str, Any],
    project_dir: str | Path,
    dry_run: bool = False,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Execute a recipe and return a result dict.

    On error in any step, halts immediately and returns an error result.
    Telemetry is always recorded (success or failure) with env refs redacted.
    Per-step JSONL telemetry is written to <state_dir>/recipes/<recipe>/<run_id>.jsonl.
    If token_budget is set, halts before dispatching any step that would exceed it.
    """
    project_dir_str = str(project_dir)

    # D — L3 readiness gate: refuse unattended execution when any of the five
    # pre-run checks fail.  Non-L3 recipes (tier None / L1 / L2) skip this.
    # Wired here, before any step resolution, so the gate fires even when
    # capabilities are unresolved (check (a) covers that).
    if recipe.tier == "L3":
        from vise.recipes.readiness import check_readiness as _check_readiness
        readiness = _check_readiness(recipe, project_dir_str)
        if not readiness["ready"]:
            failed_str = "; ".join(readiness["failed_checks"])
            log.error("[recipes][tier:L3] readiness gate BLOCKED: %s", failed_str)
            return {
                "success": False,
                "error": f"L3 readiness failed: {failed_str}",
                "readiness": readiness,
            }

    assignments = load_capabilities(project_dir_str)
    user_pins = load_user_pins(project_dir_str)

    # Resolve state_dir for JSONL telemetry
    try:
        from vise.engines.graph_state import _get_centralized_state_dir
        state_dir = str(_get_centralized_state_dir(project_dir_str))
    except Exception:
        state_dir = str(Path(project_dir_str) / ".vise" / "state")

    writer = StepTelemetryWriter(state_dir, recipe.name)
    budget = BudgetTracker(token_budget)

    step_outputs: dict[str, Any] = {}
    start_ms = time.monotonic() * 1000

    for step in recipe.steps:
        # Resolve capability
        resolved = resolve_capability(step.capability, assignments, user_pins)
        if resolved is None:
            error_msg = (
                f"step '{step.id}': capability '{step.capability}' is unresolved — "
                "use capability_set to assign a tool"
            )
            log.error("[recipes] %s", error_msg)
            duration_ms = int(time.monotonic() * 1000 - start_ms)
            _record_telemetry(project_dir_str, recipe.name, "success", False)
            _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": None,
                "resolved_tool": None,
                "rendered_args_redacted": {},
                "arg_tokens": 0,
                "duration_ms": duration_ms,
                "ok": False,
                "error": error_msg,
            })
            return {
                "success": False,
                "error": error_msg,
                "step": step.id,
                "telemetry_path": writer.path,
                "run_id": writer.run_id,
            }

        mcp_name, tool_name = resolved

        # Tier enforcement — only when the recipe declares a tier.
        # Recipes without a tier field run without restriction (backward compat).
        if recipe.tier is not None:
            from vise.recipes.tiers import check_step as _tier_check
            if not _tier_check(recipe.tier, step.capability):
                duration_ms = int(time.monotonic() * 1000 - start_ms)
                if recipe.tier == "L2":
                    # L2: halt and return a pause signal for human approval.
                    log.info(
                        "[recipes][tier:L2] halting at step=%s capability=%s — paused for approval",
                        step.id, step.capability,
                    )
                    _record_telemetry(project_dir_str, recipe.name, "success", False)
                    _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
                    writer.write({
                        "ts": _utc_now_iso(),
                        "run_id": writer.run_id,
                        "recipe": recipe.name,
                        "step_id": step.id,
                        "capability": step.capability,
                        "resolved_mcp": mcp_name,
                        "resolved_tool": tool_name,
                        "rendered_args_redacted": {},
                        "arg_tokens": 0,
                        "duration_ms": duration_ms,
                        "ok": False,
                        "error": f"tier:L2 paused before sideeffect cap '{step.capability}'",
                    })
                    return {
                        "success": False,
                        "paused_for_approval": step.id,
                        "capability": step.capability,
                        "telemetry_path": writer.path,
                        "run_id": writer.run_id,
                    }
                else:
                    # L1 (or any other tier with a deny): hard error.
                    error_msg = (
                        f"step '{step.id}': capability '{step.capability}' is not "
                        f"permitted at tier {recipe.tier}"
                    )
                    log.error("[recipes] %s", error_msg)
                    _record_telemetry(project_dir_str, recipe.name, "success", False)
                    _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
                    writer.write({
                        "ts": _utc_now_iso(),
                        "run_id": writer.run_id,
                        "recipe": recipe.name,
                        "step_id": step.id,
                        "capability": step.capability,
                        "resolved_mcp": mcp_name,
                        "resolved_tool": tool_name,
                        "rendered_args_redacted": {},
                        "arg_tokens": 0,
                        "duration_ms": duration_ms,
                        "ok": False,
                        "error": error_msg,
                    })
                    return {
                        "success": False,
                        "error": error_msg,
                        "step": step.id,
                        "telemetry_path": writer.path,
                        "run_id": writer.run_id,
                    }

        # Render args
        try:
            rendered_args = render_value(step.args, inputs, step_outputs)
        except KeyError as e:
            error_msg = f"step '{step.id}': template render error: {e}"
            log.error("[recipes] %s", error_msg)
            duration_ms = int(time.monotonic() * 1000 - start_ms)
            _record_telemetry(project_dir_str, recipe.name, "success", False)
            _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": mcp_name,
                "resolved_tool": tool_name,
                "rendered_args_redacted": {},
                "arg_tokens": 0,
                "duration_ms": duration_ms,
                "ok": False,
                "error": error_msg,
            })
            return {
                "success": False,
                "error": error_msg,
                "step": step.id,
                "telemetry_path": writer.path,
                "run_id": writer.run_id,
            }

        arg_tokens = count_tokens(rendered_args if isinstance(rendered_args, dict) else {})
        rendered_args_redacted = redact_for_telemetry(rendered_args) if isinstance(rendered_args, dict) else {}
        budget.add(arg_tokens)

        budget_error = budget.check()
        if budget_error:
            log.error("[recipes] %s", budget_error)
            duration_ms = int(time.monotonic() * 1000 - start_ms)
            _record_telemetry(project_dir_str, recipe.name, "success", False)
            _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": mcp_name,
                "resolved_tool": tool_name,
                "rendered_args_redacted": rendered_args_redacted,
                "arg_tokens": arg_tokens,
                "duration_ms": duration_ms,
                "ok": False,
                "error": budget_error,
            })
            return {
                "success": False,
                "error": budget_error,
                "step": step.id,
                "telemetry_path": writer.path,
                "run_id": writer.run_id,
            }

        if dry_run:
            log.info(
                "[recipes][dry_run] step=%s capability=%s -> %s.%s args=%r",
                step.id, step.capability, mcp_name, tool_name, rendered_args,
            )
            step_outputs[step.id] = {"dry_run": True}
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": mcp_name,
                "resolved_tool": tool_name,
                "rendered_args_redacted": rendered_args_redacted,
                "arg_tokens": arg_tokens,
                "duration_ms": 0,
                "ok": True,
            })
            continue

        # Dispatch
        log.info(
            "[recipes] step=%s capability=%s -> %s.%s",
            step.id, step.capability, mcp_name, tool_name,
        )

        step_start = time.monotonic() * 1000
        try:
            if step.capability == "meta.assert":
                output = meta_assert(rendered_args)
            else:
                output = await _call_tool(mcp_name, tool_name, rendered_args)
        except AssertionError as e:
            error_msg = f"step '{step.id}' assertion failed: {e}"
            log.error("[recipes] %s", error_msg)
            duration_ms = int(time.monotonic() * 1000 - start_ms)
            step_duration_ms = int(time.monotonic() * 1000 - step_start)
            _record_telemetry(project_dir_str, recipe.name, "success", False)
            _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": mcp_name,
                "resolved_tool": tool_name,
                "rendered_args_redacted": rendered_args_redacted,
                "arg_tokens": arg_tokens,
                "duration_ms": step_duration_ms,
                "ok": False,
                "error": error_msg,
            })
            return {
                "success": False,
                "error": error_msg,
                "step": step.id,
                "telemetry_path": writer.path,
                "run_id": writer.run_id,
            }
        except Exception as e:
            error_msg = f"step '{step.id}': tool call failed: {e}"
            log.error("[recipes] %s", error_msg)
            duration_ms = int(time.monotonic() * 1000 - start_ms)
            step_duration_ms = int(time.monotonic() * 1000 - step_start)
            _record_telemetry(project_dir_str, recipe.name, "success", False)
            _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": mcp_name,
                "resolved_tool": tool_name,
                "rendered_args_redacted": rendered_args_redacted,
                "arg_tokens": arg_tokens,
                "duration_ms": step_duration_ms,
                "ok": False,
                "error": error_msg,
            })
            return {
                "success": False,
                "error": error_msg,
                "step": step.id,
                "telemetry_path": writer.path,
                "run_id": writer.run_id,
            }

        # Unwrap the MCP JSON-RPC envelope (subprocess proxies) before deciding
        # success — otherwise a real ok:false / error sails through because the
        # error/ok keys live under result.structuredContent, not at top level.
        from vise.engines.validators import _unwrap_tool_output

        unwrapped, is_error = _unwrap_tool_output(output)

        # Check for tool-level failure in (unwrapped) response.
        step_error: str | None = None
        if is_error:
            step_error = f"step '{step.id}': tool reported isError"
        elif isinstance(unwrapped, dict) and "error" in unwrapped:
            step_error = f"step '{step.id}': tool returned error: {unwrapped['error']}"
        elif isinstance(unwrapped, dict) and "ok" in unwrapped and not unwrapped.get("ok"):
            step_error = f"step '{step.id}': tool returned ok=false: {unwrapped!r}"[:300]
        elif isinstance(unwrapped, dict) and unwrapped.get("status") == "unresolved":
            step_error = (
                f"step '{step.id}': dispatch unresolved: "
                f"{unwrapped.get('reason', 'no MCP dispatch layer')}"
            )

        if step_error is not None:
            log.error("[recipes] %s", step_error)
            duration_ms = int(time.monotonic() * 1000 - start_ms)
            step_duration_ms = int(time.monotonic() * 1000 - step_start)
            _record_telemetry(project_dir_str, recipe.name, "success", False)
            _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)
            writer.write({
                "ts": _utc_now_iso(),
                "run_id": writer.run_id,
                "recipe": recipe.name,
                "step_id": step.id,
                "capability": step.capability,
                "resolved_mcp": mcp_name,
                "resolved_tool": tool_name,
                "rendered_args_redacted": rendered_args_redacted,
                "arg_tokens": arg_tokens,
                "duration_ms": step_duration_ms,
                "ok": False,
                "error": step_error,
            })
            return {
                "success": False,
                "error": step_error,
                "step": step.id,
                "telemetry_path": writer.path,
                "run_id": writer.run_id,
            }

        step_duration_ms = int(time.monotonic() * 1000 - step_start)
        writer.write({
            "ts": _utc_now_iso(),
            "run_id": writer.run_id,
            "recipe": recipe.name,
            "step_id": step.id,
            "capability": step.capability,
            "resolved_mcp": mcp_name,
            "resolved_tool": tool_name,
            "rendered_args_redacted": rendered_args_redacted,
            "arg_tokens": arg_tokens,
            "duration_ms": step_duration_ms,
            "ok": True,
        })

        # Bind the UNWRAPPED result for downstream {{ steps.ID.output.K }} refs
        # so consumers see the actual tool output, not the JSON-RPC envelope.
        step_outputs[step.id] = unwrapped if isinstance(unwrapped, dict) else {"result": unwrapped}

    duration_ms = int(time.monotonic() * 1000 - start_ms)
    _record_telemetry(project_dir_str, recipe.name, "success", True)
    _record_telemetry(project_dir_str, recipe.name, "duration_ms", duration_ms)

    return {
        "success": True,
        "recipe": recipe.name,
        "duration_ms": duration_ms,
        "outputs": step_outputs,
        "dry_run": dry_run,
        "telemetry_path": writer.path,
        "run_id": writer.run_id,
    }


async def _call_tool(mcp_name: str, tool_name: str, args: dict) -> Any:
    """Tool dispatch seam.

    vise ships no MCP proxy dispatch layer (jig's internal_proxy/proxy_pool
    were deliberately not extracted). Capability calls that resolve to an
    external MCP tool return a structured failure instead of crashing.
    Tests and in-host embedders monkeypatch this function to inject a real
    dispatcher.
    """
    return {
        "status": "unresolved",
        "reason": (
            "no MCP dispatch layer — bind capabilities to built-in handlers "
            "or run in-host"
        ),
        "mcp_name": mcp_name,
        "tool_name": tool_name,
    }
