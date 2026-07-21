"""Surface tools for the cross-MCP recipe system.

Registered tools:
    recipe_list        — list recipes + per-recipe all_capabilities_resolved flag
    recipe_describe    — show steps with resolved (mcp, tool) per capability
    recipe_run         — execute a recipe; halt on first error
    capability_set     — assign a capability to a tool (mcp_name.tool_name)
    capability_audit   — list capabilities with no registered tool
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP

from vise.core.session import resolve_project_dir
from vise.recipes.autotag import suggest_capability
from vise.recipes.capabilities import INTERNAL_BINDINGS
from vise.recipes.loader import load_capabilities, load_recipes, load_user_pins
from vise.recipes.resolver import resolve_capability

log = logging.getLogger(__name__)


def _resolution_source(
    capability: str,
    assignments: dict[str, str],
    user_pins: dict[str, str],
) -> str:
    """Classify why a capability resolved (or didn't): user_pin > assignment > internal > unresolved."""
    if capability in user_pins:
        return "user_pin"
    for _tool, cap in assignments.items():
        if cap == capability:
            return "assignment"
    if capability in INTERNAL_BINDINGS:
        return "internal_binding"
    return "unresolved"


def _autotag_suggestions_for_capability(
    capability: str,
    assignments: dict[str, str],
    *,
    limit: int = 3,
) -> list[dict[str, object]]:
    """For an unresolved capability, scan registered proxy tools (excluding already-assigned)
    and return tool suggestions whose autotag picks this capability with confidence."""
    try:
        from vise.core import embed_cache
    except ImportError:
        return []
    try:
        records = embed_cache.list_tools()
    except Exception:
        return []
    out: list[dict[str, object]] = []
    for rec in records:
        tool_id = f"{rec.mcp_name}.{rec.tool_name}"
        if tool_id in assignments:
            continue
        result = suggest_capability(rec.description or "")
        if result is None or result.capability != capability:
            continue
        out.append({
            "tool": tool_id,
            "score": round(result.score, 3),
            "confident": result.confident,
            "runner_up": result.runner_up,
        })
    out.sort(key=lambda r: r["score"], reverse=True)  # type: ignore[arg-type, return-value]
    return out[:limit]


def explain_recipe(
    name: str,
    project_dir: str,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the resolution chain for a recipe (no execution)."""
    recipes = load_recipes(project_dir)
    recipe = next((r for r in recipes if r.name == name), None)
    if recipe is None:
        return {"success": False, "error": f"recipe '{name}' not found", "project_dir": project_dir}
    assignments = load_capabilities(project_dir)
    user_pins = load_user_pins(project_dir)
    chain: list[dict[str, Any]] = []
    for s in recipe.steps:
        resolved = resolve_capability(s.capability, assignments, user_pins)
        source = _resolution_source(s.capability, assignments, user_pins)
        entry: dict[str, Any] = {
            "id": s.id,
            "capability": s.capability,
            "resolution_source": source,
            "resolved_mcp": resolved[0] if resolved else None,
            "resolved_tool": resolved[1] if resolved else None,
            "args_template": s.args,
        }
        if resolved is None:
            entry["suggestions"] = _autotag_suggestions_for_capability(s.capability, assignments)
        chain.append(entry)
    return {
        "success": True,
        "recipe": recipe.name,
        "scope": recipe.scope,
        "source_path": str(recipe.source_path),
        "inputs_required": recipe.inputs,
        "inputs_provided": list((inputs or {}).keys()),
        "resolution_chain": chain,
        "explain": True,
        "project_dir": project_dir,
    }


def audit_capabilities(
    project_dir: str,
    *,
    include_low_confidence: bool = True,
    include_conflicts: bool = True,
) -> dict[str, Any]:
    """Audit capability resolution: unresolved + low_confidence + conflicts."""
    recipes = load_recipes(project_dir)
    assignments = load_capabilities(project_dir)
    user_pins = load_user_pins(project_dir)

    unresolved: dict[str, list[str]] = {}
    for r in recipes:
        for s in r.steps:
            if resolve_capability(s.capability, assignments, user_pins) is None:
                unresolved.setdefault(s.capability, []).append(r.name)
    unresolved_rows = [
        {"capability": cap, "used_in_recipes": names}
        for cap, names in sorted(unresolved.items())
    ]

    low_confidence: list[dict[str, Any]] = []
    if include_low_confidence:
        try:
            from vise.core import embed_cache
            records = embed_cache.list_tools()
        except Exception:
            records = []
        for rec in records:
            tool_id = f"{rec.mcp_name}.{rec.tool_name}"
            if tool_id in assignments:
                continue
            result = suggest_capability(rec.description or "")
            if result is None or result.confident:
                continue
            low_confidence.append({
                "tool": tool_id,
                "top_capability": result.capability,
                "score": round(result.score, 3),
                "runner_up": result.runner_up,
                "runner_up_score": round(result.runner_up_score, 3),
                "gap": round(result.score - result.runner_up_score, 3),
            })
        low_confidence.sort(key=lambda r: r["gap"])  # type: ignore[arg-type, return-value]

    conflicts: list[dict[str, Any]] = []
    if include_conflicts:
        assignment_caps: dict[str, list[str]] = {}
        for tool, cap in assignments.items():
            assignment_caps.setdefault(cap, []).append(tool)
        for cap, pinned in user_pins.items():
            assigned_tools = assignment_caps.get(cap, [])
            if assigned_tools and pinned not in assigned_tools:
                conflicts.append({
                    "capability": cap,
                    "user_pin": pinned,
                    "assigned_tools": assigned_tools,
                    "kind": "pin_vs_assignment",
                })

    return {
        "unresolved": unresolved_rows,
        "count": len(unresolved_rows),
        "low_confidence": low_confidence,
        "low_confidence_count": len(low_confidence),
        "conflicts": conflicts,
        "conflicts_count": len(conflicts),
        "project_dir": project_dir,
    }


def register_recipes(mcp: FastMCP) -> None:

    @mcp.tool()
    def recipe_list(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """List all recipes available in the project, with a capability-resolved flag.

        Args:
            project_dir: Project directory (auto-detected if omitted).
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)
        recipes = load_recipes(resolved_dir)
        assignments = load_capabilities(resolved_dir)
        user_pins = load_user_pins(resolved_dir)

        rows = []
        for r in recipes:
            all_resolved = all(
                resolve_capability(s.capability, assignments, user_pins) is not None
                for s in r.steps
            )
            rows.append({
                "name": r.name,
                "description": r.description,
                "steps": len(r.steps),
                "inputs": r.inputs,
                "all_capabilities_resolved": all_resolved,
                "scope": r.scope,
                "source_path": str(r.source_path),
            })

        return {
            "recipes": rows,
            "count": len(rows),
            "project_dir": resolved_dir,
            "session_id": sid,
        }

    @mcp.tool()
    def recipe_describe(
        name: str,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Show a recipe's steps with resolved (mcp, tool) for each capability.

        Args:
            name: Recipe name to describe.
            project_dir: Project directory (auto-detected if omitted).
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)
        recipes = load_recipes(resolved_dir)
        recipe = next((r for r in recipes if r.name == name), None)
        if recipe is None:
            return {"error": f"recipe '{name}' not found", "project_dir": resolved_dir}

        assignments = load_capabilities(resolved_dir)
        user_pins = load_user_pins(resolved_dir)

        steps_out = []
        for s in recipe.steps:
            resolved = resolve_capability(s.capability, assignments, user_pins)
            steps_out.append({
                "id": s.id,
                "capability": s.capability,
                "description": s.description,
                "args_template": s.args,
                "resolved_mcp": resolved[0] if resolved else None,
                "resolved_tool": resolved[1] if resolved else None,
                "resolved": resolved is not None,
            })

        return {
            "name": recipe.name,
            "description": recipe.description,
            "inputs": recipe.inputs,
            "steps": steps_out,
            "project_dir": resolved_dir,
            "session_id": sid,
        }

    @mcp.tool()
    async def recipe_run(
        name: str,
        inputs: dict[str, Any] | None = None,
        dry_run: bool = False,
        explain: bool = False,
        token_budget: int | None = None,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a recipe. Halts on first error.

        Args:
            name: Recipe name to run.
            inputs: Dict of input values referenced as {{ inputs.X }} in step args.
            dry_run: If True, resolves and renders but does not call any tool.
            explain: If True, return resolution chain (recipe origin, per-step
                source/suggestions) without executing. Implies dry_run.
            token_budget: Optional max combined arg-tokens; runner halts before
                exceeding.
            project_dir: Project directory (auto-detected if omitted).
        """
        from vise.recipes.runner import run_recipe

        resolved_dir, sid = resolve_project_dir(project_dir, session_id)
        recipes = load_recipes(resolved_dir)
        recipe = next((r for r in recipes if r.name == name), None)
        if recipe is None:
            return {"success": False, "error": f"recipe '{name}' not found", "project_dir": resolved_dir}

        if explain:
            out = explain_recipe(name, resolved_dir, inputs)
            out["session_id"] = sid
            return out

        result = await run_recipe(
            recipe, inputs or {}, resolved_dir,
            dry_run=dry_run, token_budget=token_budget,
        )
        result["project_dir"] = resolved_dir
        result["session_id"] = sid
        return result

    @mcp.tool()
    def capability_set(
        tool: str,
        capability: str | None,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign or clear a capability for a tool.

        Args:
            tool: Tool identifier in ``mcp_name.tool_name`` format
                  (e.g. ``"firecrawl.scrape_url"``).
            capability: Capability string (e.g. ``"web.scrape"``).
                        Pass ``null`` to clear the assignment.
            project_dir: Project directory (auto-detected if omitted).
        """
        from vise.recipes.capabilities import validate_capability

        resolved_dir, sid = resolve_project_dir(project_dir, session_id)
        cap_path = Path(resolved_dir) / ".vise" / "capabilities.yaml"
        cap_path.parent.mkdir(parents=True, exist_ok=True)

        # Validate format
        if "." not in tool:
            return {
                "success": False,
                "error": f"tool '{tool}' must be in 'mcp_name.tool_name' format",
            }

        if capability is not None and not validate_capability(capability):
            return {
                "success": False,
                "error": (
                    f"capability '{capability}' is not in the taxonomy and "
                    "does not use the x.* extension namespace"
                ),
            }

        # Load existing
        assignments = {}
        if cap_path.exists():
            try:
                raw = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    assignments = {str(k): v for k, v in raw.items()}
            except yaml.YAMLError:
                pass

        if capability is None:
            assignments.pop(tool, None)
            action = "cleared"
        else:
            assignments[tool] = capability
            action = "set"

        cap_path.write_text(yaml.dump(assignments, default_flow_style=False), encoding="utf-8")

        return {
            "success": True,
            "tool": tool,
            "capability": capability,
            "action": action,
            "project_dir": resolved_dir,
            "session_id": sid,
        }

    @mcp.tool()
    def capability_audit(
        project_dir: str | None = None,
        session_id: str | None = None,
        include_low_confidence: bool = True,
        include_conflicts: bool = True,
    ) -> dict[str, Any]:
        """Audit capability resolution across recipes.

        Returns:
            unresolved: capabilities used in recipes with no resolution.
            low_confidence: tools whose autotag suggestion is non-confident
                (close-call between two capabilities).
            conflicts: capabilities where a user pin disagrees with an existing
                assignment for the same capability.

        Args:
            include_low_confidence: scan registered proxy tools for ambiguous
                autotag suggestions. Costs an embedding pass per unassigned tool.
            include_conflicts: include user-pin / assignment conflicts.
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)
        out = audit_capabilities(
            resolved_dir,
            include_low_confidence=include_low_confidence,
            include_conflicts=include_conflicts,
        )
        out["session_id"] = sid
        return out


# Back-compat alias
register_recipe_tools = register_recipes
