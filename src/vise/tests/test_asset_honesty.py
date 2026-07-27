"""Bundled assets must only reference tools that exist in vise's MCP surface.

Two failure modes this locks down:
  1. A workflow node instructing the model to call a tool vise does not
     expose (``execute_mcp_tool``, ``next_task_record`` — both predecessor
     leftovers).
     A rewrite that removes a whole strategy can also leave dangling edges,
     so edge integrity is asserted here too (test_bundled_workflows.py only
     checks that nodes/edges are non-empty).
  2. A capability bound to a nonexistent MCP, which makes a gate report a
     false green instead of surfacing the GAP.
"""

from pathlib import Path

import pytest

from vise.engines.graph_parser import load_graph_from_file
from vise.recipes.capabilities import INTERNAL_BINDINGS
from vise.recipes.loader import Recipe, RecipeStep
from vise.recipes.resolver import resolve_capability
from vise.recipes.runner import run_recipe

ASSETS = Path(__file__).resolve().parents[1] / "assets"
WORKFLOW_FILES = sorted((ASSETS / "workflows").glob("*.yaml"))

# Tools carried over from the predecessor orchestrator. None exist in vise.
DEAD_TOOLS = ("execute_mcp_tool", "next_task_record", "next_task_get", "proxy_tools_search")

# vise's real MCP surface — the only thing INTERNAL_BINDINGS may point at.
VISE_TOOLS = frozenset({
    "capability_audit", "capability_set",
    "experience_derive_checklist", "experience_list", "experience_query",
    "experience_record", "experience_stats",
    "goal_abandon", "goal_bootstrap", "goal_clear", "goal_complete",
    "goal_get", "goal_set", "goal_validate",
    "graph_activate", "graph_builder_add_edge", "graph_builder_add_node",
    "graph_builder_create", "graph_builder_delete", "graph_builder_list",
    "graph_builder_preview", "graph_builder_save", "graph_builder_update_edge",
    "graph_builder_update_node", "graph_check_phrase", "graph_check_tool",
    "graph_deactivate", "graph_enforcer_status", "graph_enforcer_toggle",
    "graph_get_ready_tasks", "graph_list_available", "graph_override_max_visits",
    "graph_record_output", "graph_reset", "graph_set_node", "graph_status",
    "graph_task_complete", "graph_timeline", "graph_traverse", "graph_validate",
    "graph_visualize",
    "recipe_describe", "recipe_list", "recipe_run",
    "snapshot_create", "snapshot_diff", "snapshot_list", "snapshot_restore",
    "vise_version",
})


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("yaml_path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_workflow_names_no_dead_tools(yaml_path: Path) -> None:
    text = yaml_path.read_text(encoding="utf-8")
    found = [t for t in DEAD_TOOLS if t in text]
    assert not found, f"{yaml_path.name} instructs the model to call {found} — no such vise tool"


@pytest.mark.parametrize("yaml_path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_workflow_edges_point_at_real_nodes(yaml_path: Path) -> None:
    graph = load_graph_from_file(yaml_path)
    node_ids = {n.id for n in graph.nodes.values()} if isinstance(graph.nodes, dict) else {n.id for n in graph.nodes}
    edges = graph.edges.values() if isinstance(graph.edges, dict) else graph.edges
    dangling = [
        (e.id, e.from_node, e.to_node)
        for e in edges
        if e.from_node not in node_ids or e.to_node not in node_ids
    ]
    assert not dangling, f"{yaml_path.name} has edges pointing at missing nodes: {dangling}"


# ---------------------------------------------------------------------------
# Capability bindings
# ---------------------------------------------------------------------------

def test_internal_bindings_only_reference_real_vise_tools() -> None:
    bogus = {
        cap: (mcp, tool)
        for cap, (mcp, tool) in INTERNAL_BINDINGS.items()
        if tool not in VISE_TOOLS
    }
    assert not bogus, f"bindings to nonexistent tools (would report a false green): {bogus}"


@pytest.mark.parametrize(
    "capability",
    ["meta.list", "meta.notion_drift", "validate.integration.e2e"],
)
def test_gap_first_capabilities_resolve_unbound(capability: str) -> None:
    # These ship UNBOUND on purpose so the gate surfaces the GAP.
    assert resolve_capability(capability, {}, {}) is None


async def test_unbound_capability_fails_recipe_loudly(tmp_path: Path) -> None:
    recipe = Recipe(
        name="unbound-probe",
        description="single step on a deliberately unbound capability",
        inputs=[],
        steps=[RecipeStep(id="e2e", capability="validate.integration.e2e", args={})],
        source_path=tmp_path / "unbound-probe.yaml",
    )
    result = await run_recipe(recipe, {}, tmp_path)

    assert result["success"] is False
    assert "unresolved" in result["error"]
    assert "capability_set" in result["error"]
    # "fails loudly" and "returns a plan" are adjacent shapes now that
    # run_recipe emits a plan for resolved steps — an unbound step must
    # never carry a plan entry alongside the failure.
    assert not result.get("plan")
