"""Per-node validator gate — provider-independent.

Runs the declared validators (tests_pass / lint_pass / command_exit /
files_exist / capability / lsp_clean) from ``vise.engines.validators``
and optional recipe gates as a binary pass-all gate.
"""
from __future__ import annotations

from typing import Any


async def _run_node_validators(node: Any, project_dir: str, state: Any = None) -> dict | None:
    """Run a node's declared validators / recipe as a binary pass-all gate.

    Returns None when the node declares no gate. Otherwise returns a dict:
        {"passed": bool, "failed_count": int,
         "failed": [{"name", "evidence", "exit_code"}], "confidence": float}
    """
    if not node or not (getattr(node, "validators", None) or getattr(node, "recipe", None)):
        return None

    import asyncio
    import sys
    from types import SimpleNamespace

    from vise.engines.validators import aggregate_confidence, build_validators

    goal_like = SimpleNamespace(project_dir=project_dir, id=f"node:{node.id}")
    results = []

    # 1. Inline validator configs (same shape as goal validator_configs).
    if getattr(node, "validators", None):
        try:
            vs = build_validators(node.validators)
            for v in vs:
                if hasattr(v, "run_async"):
                    results.append(await v.run_async(goal_like))
                else:
                    results.append(await asyncio.to_thread(v.run, goal_like))
        except Exception as e:
            results.append(SimpleNamespace(
                name="validators", passed=False, weight=1.0,
                evidence=f"validator build/run error: {e}"[:300], exit_code=None,
            ))

    # 2. Recipe gate — vise ships no recipe engine; a declared recipe fails
    #    the gate with explicit evidence rather than silently passing.
    if getattr(node, "recipe", None):
        try:
            from vise.recipes.loader import load_recipes
            from vise.recipes.runner import run_recipe

            recipes = {r.name: r for r in load_recipes(project_dir)}
            recipe_obj = recipes.get(node.recipe)
            if recipe_obj is None:
                results.append(SimpleNamespace(
                    name=f"recipe:{node.recipe}", passed=False, weight=1.0,
                    evidence=f"recipe '{node.recipe}' not found", exit_code=None,
                ))
            else:
                rec_result = await run_recipe(recipe_obj, {}, project_dir)
                rec_passed = bool(rec_result.get("success"))
                results.append(SimpleNamespace(
                    name=f"recipe:{node.recipe}", passed=rec_passed, weight=1.0,
                    evidence=(rec_result.get("error") or "recipe ok")[:300],
                    exit_code=0 if rec_passed else 1,
                ))
        except Exception as e:
            results.append(SimpleNamespace(
                name=f"recipe:{node.recipe}", passed=False, weight=1.0,
                evidence=f"recipe run error: {e}"[:300], exit_code=None,
            ))

    passed = all(getattr(r, "passed", False) for r in results) if results else True
    failed = [r for r in results if not getattr(r, "passed", False)]

    if not passed:
        try:
            _record_node_gate_failure(project_dir, node.id, failed)
        except Exception as e:
            print(f"[vise] Warning: failed to record node-gate failure lesson: {e}", file=sys.stderr)

    return {
        "passed": passed,
        "failed_count": len(failed),
        "failed": [
            {
                "name": getattr(r, "name", "?"),
                "evidence": getattr(r, "evidence", ""),
                "exit_code": getattr(r, "exit_code", None),
            }
            for r in failed
        ],
        "confidence": aggregate_confidence(results) if results else 1.0,
    }


def _record_node_gate_failure(project_dir: str, node_id: str, failed: list) -> None:
    """Fail-safe: record a node-gate failure lesson into experience memory."""
    from pathlib import Path

    try:
        from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore
    except Exception:
        return
    project_name = Path(project_dir).name
    store = ExperienceMemoryStore()
    try:
        store.load(scope="project", project_name=project_name)
    except Exception:
        return
    names = ", ".join(getattr(r, "name", "?") for r in failed) or "?"
    evidence = "; ".join(
        f"{getattr(r, 'name', '?')}: {getattr(r, 'evidence', '')}" for r in failed
    )[:800]
    try:
        store.record(
            ExperienceEntry(
                type="smell_introduced",
                file_pattern=f"node:{node_id}",
                keywords=["node-gate-failure", node_id][:10],
                description=f"Node gate failed at '{node_id}': {names}"[:200],
                severity="high",
                resolution=f"Node gate failed: {evidence}"[:800],
                project_origin=project_name,
                scope="project",
            )
        )
    except Exception:
        return
