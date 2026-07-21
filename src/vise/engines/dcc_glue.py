"""Code-analysis provider glue — vise ships no provider; tension gate and
DCC experience collection are no-ops that report 'no provider'.

This module preserves the exact symbol surface the graph tools import
(``tools/_graph_query.py``, ``tools/_graph_mutation.py``,
``tools/_graph_management.py``, ``tools/_graph_transition.py``) so the
workflow engine degrades gracefully when no code-analysis backend is
installed: gates open, analyses return empty, nothing crashes.

To plug a provider in, replace these functions with a real implementation
(jig's dcc_gate_enforcer / dcc_analysis_executor / mcp_execution_layer
split is the reference architecture).
"""
from __future__ import annotations

from typing import Any

_NO_PROVIDER = "no provider"


# ---------------------------------------------------------------------------
# Availability / execution layer
# ---------------------------------------------------------------------------

def _is_dcc_available() -> bool:
    """No code-analysis provider is bundled with vise."""
    return False


def _is_livespec_available() -> bool:
    return False


async def _execute_dcc_tool(tool_name: str, args: dict, project_dir: str) -> None:
    """No provider — every tool call resolves to None."""
    return None


async def _run_livespec_reindex(project_dir: str, force: bool = False) -> None:
    return None


# ---------------------------------------------------------------------------
# Analysis executor
# ---------------------------------------------------------------------------

def _resolve_dcc_config(node: Any, enforcer_config: dict) -> tuple[bool, list, int]:
    """(should_run, analyses, token_budget) — never run: no provider."""
    return (False, [], 0)


async def _run_dcc_reindex_incremental(project_dir: str, since_sha: str | None = None) -> None:
    return None


async def _run_dcc_reindex(project_dir: str) -> None:
    return None


async def _run_dcc_analysis(
    analyses: list, token_budget: int, project_dir: str
) -> tuple[None, None]:
    """(formatted_result, raw_result) — both None: no provider."""
    return (None, None)


async def _detect_project_languages(project_dir: str) -> list[str]:
    return []


async def _run_mid_phase_check(
    project_dir: str, files: list[str], baseline_smells: list | None = None
) -> dict:
    return {"status": _NO_PROVIDER, "smells": [], "new_smells": []}


# ---------------------------------------------------------------------------
# Tension gate — always open (no tensions without a provider)
# ---------------------------------------------------------------------------

async def _check_tension_gate(node: Any, project_dir: str, state: Any = None) -> None:
    """Gate open: no provider means no tensions to block on."""
    return None


async def _run_pre_transition_check(
    node: Any, project_dir: str, baseline_smells: list | None = None
) -> None:
    return None


async def _run_impact_preview(
    node: Any, project_dir: str, entry_sha: str | None = None
) -> None:
    return None


def _get_tension_gate_info(
    node: Any, project_dir: str, node_id: str | None, state: Any = None
) -> None:
    return None


def _clear_tension_gate_state(state_or_project_dir: Any, node_id: str | None = None) -> None:
    return None


def acknowledge_tension_gate(project_dir: str, node_id: str, state: Any = None) -> dict:
    return {"acknowledged": True, "node_id": node_id, "note": _NO_PROVIDER}


# ---------------------------------------------------------------------------
# Experience collection / skill enrichment — empty
# ---------------------------------------------------------------------------

def _collect_experiences_from_dcc(dcc_raw: Any, project_dir: str) -> list:
    return []

def _query_relevant_experiences(dcc_raw: Any, project_dir: str) -> list:
    return []


def _enrich_smells_with_skills(dcc_raw: Any, detected_langs: list) -> dict:
    return {}


def _select_skills_for_context(skill_recs: dict, node: Any, detected_langs: list) -> list:
    return []


def _record_skill_references(skill_recs: dict, project_dir: str) -> None:
    return None


# ---------------------------------------------------------------------------
# Summarize helpers
# ---------------------------------------------------------------------------

def _extract_tensions(raw: Any) -> list:
    return []


def smells_for_files(paths: list | set, *, max_results: int = 5) -> list:
    return []


# ---------------------------------------------------------------------------
# Node validators — REAL implementation (provider-independent).
#
# The per-node validator gate does not need a code-analysis provider: it
# runs the declared validators (tests_pass / lint_pass / command_exit /
# files_exist / capability / lsp_clean) from ``vise.engines.validators``.
# Ported verbatim from jig's dcc_gate_enforcer.
# ---------------------------------------------------------------------------

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
