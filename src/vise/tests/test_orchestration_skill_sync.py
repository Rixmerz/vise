"""The orchestration skill routes to workflows by name — guard those names.

Step 0 of skills/orchestration/SKILL.md is a routing table mapping a kind of
request to a `graph_activate(graph_name=...)` call. That table is prose: nothing
executes it, so a workflow rename or deletion leaves the skill telling every
agent to activate something that no longer exists, and the failure surfaces as
a confusing tool error in the middle of someone's task.

This is the same drift that hit the README's `[dev]` list and its tool count —
a fact restated in prose, diverging from the source. Same fix: assert it.

Also pins the two `tools_blocked` claims the skill makes as load-bearing
guidance, because an agent that believes it can dispatch under debug-graph will
plan a wave that cannot run.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_SKILL = _REPO / "skills" / "orchestration" / "SKILL.md"
_WORKFLOWS = _REPO / "src" / "vise" / "assets" / "workflows"


def _bundled_graph_names() -> set[str]:
    """The names `graph_activate` accepts — filename minus the -graph suffix."""
    return {p.name.removesuffix("-graph.yaml") for p in _WORKFLOWS.glob("*-graph.yaml")}


def _routed_names() -> list[str]:
    """Every `graph_activate(graph_name=X)` target named in the skill's table.

    The table cells are backticked bare names; the surrounding prose also names
    graphs (`debug-graph`), so match only the code-span form used in the table
    column, which is what an agent copies into the call.
    """
    text = _SKILL.read_text(encoding="utf-8")
    table = text.split("## Step 0")[1].split("### The conflict rule")[0]
    return re.findall(r"\|\s*`([a-z0-9-]+)`\s*\|", table)


def test_skill_file_exists_and_has_the_routing_table():
    assert _SKILL.is_file(), f"orchestration skill missing at {_SKILL}"
    assert _routed_names(), "Step 0 routing table parsed to zero targets — did its shape change?"


def test_every_routed_workflow_actually_ships():
    bundled = _bundled_graph_names()
    missing = [n for n in _routed_names() if n not in bundled]

    assert not missing, (
        f"orchestration skill routes to workflows that do not ship: {missing}. "
        f"Bundled: {sorted(bundled)}"
    )


def test_debug_graph_blocks_task_on_every_node_but_fix():
    """The skill tells agents to delegate only once they reach `fix`."""
    graph = yaml.safe_load((_WORKFLOWS / "debug-graph.yaml").read_text(encoding="utf-8"))
    allowed = {
        n["id"] for n in graph["nodes"] if "Task" not in (n.get("tools_blocked") or [])
    }

    assert allowed == {"fix"}, (
        f"debug-graph's Task-allowing nodes changed to {sorted(allowed)}; the "
        "orchestration skill still tells agents that `fix` is the only one"
    )


@pytest.mark.parametrize("graph_name,node_id", [
    ("feature-dev", "orient"),
    ("feature-dev", "design"),
    ("pr-review", "fetch"),
])
def test_read_only_phases_named_by_the_skill_really_block_edits(graph_name, node_id):
    """The skill says builders dispatched into these phases fail on first edit."""
    graph = yaml.safe_load(
        (_WORKFLOWS / f"{graph_name}-graph.yaml").read_text(encoding="utf-8")
    )
    node = next(n for n in graph["nodes"] if n["id"] == node_id)

    assert "Edit" in (node.get("tools_blocked") or []), (
        f"{graph_name}:{node_id} no longer blocks Edit — the orchestration "
        "skill still describes it as a read-only phase"
    )
