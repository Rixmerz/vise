"""Recipe runner — turns a Recipe into an executable PLAN, step by step.

vise is an MCP *server*; it has no way to call another MCP server's tool
(that dispatch layer was deliberately removed). So instead of executing
steps, the runner resolves + renders each one and hands back an ordered
plan for the calling agent (Claude Code) to execute itself — the same
"vise advises, the agent executes" shape as `graph_traverse`'s
`prompt_injection`.

Responsibilities:
- Render step args via the template renderer
- Resolve each capability to (mcp_name, tool_name); halt loudly if unresolved
- Execute `meta.assert` locally (it needs no external MCP) when not dry_run
- Emit a plan entry for every other step instead of dispatching it
- Bind a placeholder for downstream {{ steps.ID.output.K }} references so
  they survive verbatim into later steps' rendered args
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


# A `_record_telemetry` helper fanned aggregate success/duration into
# `vise.engines.trend_tracker` — a module that never shipped, so its 14 call
# sites recorded into nothing. Per-step JSONL telemetry below is the real one.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _UnexecutedStepOutput(dict):
    """Placeholder output for a step the runner only PLANNED (didn't execute).

    Any key access echoes back the literal `{{ steps.ID.output.K }}` template
    string instead of raising KeyError, so a later step referencing this
    step's output gets the reference back verbatim in its rendered args —
    the caller substitutes the real value after it executes this step.
    """

    def __init__(self, step_id: str) -> None:
        super().__init__()
        self._step_id = step_id

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: str) -> str:
        suffix = f".{key}" if key else ""
        return f"{{{{ steps.{self._step_id}.output{suffix} }}}}"


def _has_unresolved_step_ref(value: Any) -> bool:
    """True if *value* still contains a `{{ steps.` placeholder (i.e. it
    depends on a step this runner only planned, not executed for real)."""
    if isinstance(value, str):
        return "{{ steps." in value or "{{steps." in value
    if isinstance(value, dict):
        return any(_has_unresolved_step_ref(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_unresolved_step_ref(v) for v in value)
    return False


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
    plan: list[dict[str, Any]] = []
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

        step_start = time.monotonic() * 1000

        # meta.assert needs no external MCP — run it for real unless dry_run
        # asked for a pure preview, or its args still reference a step this
        # runner only planned (the real value doesn't exist yet, so asserting
        # against the literal placeholder would be meaningless / spuriously
        # fail). Every other capability has nowhere to dispatch to (vise
        # ships no MCP proxy layer), so it becomes a plan entry instead.
        if (
            not dry_run
            and step.capability == "meta.assert"
            and not _has_unresolved_step_ref(rendered_args)
        ):
            try:
                output = meta_assert(rendered_args)
            except AssertionError as e:
                error_msg = f"step '{step.id}' assertion failed: {e}"
                log.error("[recipes] %s", error_msg)
                duration_ms = int(time.monotonic() * 1000 - start_ms)
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

            step_duration_ms = int(time.monotonic() * 1000 - step_start)
            step_outputs[step.id] = output if isinstance(output, dict) else {"result": output}
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
            continue

        # Plan step — nothing dispatched. The caller executes resolved_mcp.
        # resolved_tool with `args`, then continues the recipe.
        log.info(
            "[recipes][plan] step=%s capability=%s -> %s.%s",
            step.id, step.capability, mcp_name, tool_name,
        )
        plan.append({
            "step_id": step.id,
            "capability": step.capability,
            "resolved_mcp": mcp_name,
            "resolved_tool": tool_name,
            "args": rendered_args,
        })
        step_outputs[step.id] = _UnexecutedStepOutput(step.id)
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
            "planned": True,
        })

    duration_ms = int(time.monotonic() * 1000 - start_ms)

    return {
        "success": True,
        "recipe": recipe.name,
        "duration_ms": duration_ms,
        "outputs": step_outputs,
        "plan": plan,
        "message": (
            "vise cannot dispatch these steps itself — execute each plan "
            "entry's resolved_mcp.resolved_tool with its args, in order, "
            "substituting any '{{ steps.ID.output.K }}' placeholder with "
            "that step's real output before the next step."
        ) if plan else "all steps executed locally (no external dispatch needed).",
        "dry_run": dry_run,
        "telemetry_path": writer.path,
        "run_id": writer.run_id,
    }


async def _call_tool(mcp_name: str, tool_name: str, args: dict) -> Any:
    """Tool dispatch seam.

    vise ships no MCP proxy dispatch layer — that was a deliberate omission,
    since Claude Code's native tool discovery covers it. Capability calls that resolve to an
    external MCP tool return a structured failure instead of crashing.
    Tests and in-host embedders monkeypatch this function to inject a real
    dispatcher.

    NOT called by `run_recipe` — the recipe path never had a dispatch layer
    to fall back on and now returns a `plan` for the caller to execute
    instead of pretending to dispatch. This seam is kept solely because
    `vise.engines.validators.CapabilityValidator` (node-gate validators)
    imports and awaits it directly; removing it breaks that consumer.
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
