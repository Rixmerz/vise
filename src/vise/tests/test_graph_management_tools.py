"""The management tools an agent calls around a workflow, through the MCP surface.

``_graph_management.py`` is 613 lines and read 24%: ``graph_activate`` got
tests when it learned to say where a workflow came from, and the rest of the
module — listing, deactivating, validating, visualising, the visit override —
was reached by nothing. Same harness as the other tool-level tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_state import load_active_graph, load_graph_state, save_graph_state

from .test_graph_query_tools import DAG_GRAPH_YAML, WAVE_GRAPH_YAML


@pytest.fixture
def tools():
    registered: dict = {}

    class _FakeMCP:
        def tool(self, *a, **kw):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    from vise.tools._graph_management import register_graph_management_tools

    register_graph_management_tools(_FakeMCP())
    return registered


def _active(tmp_path: Path, yaml: str = WAVE_GRAPH_YAML) -> str:
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(yaml)
    load_active_graph(str(tmp_path))
    return str(tmp_path)


# --- graph_list_available ------------------------------------------------------


def test_the_bundled_library_is_listed_with_names(tools, tmp_path):
    out = tools["graph_list_available"](project_dir=str(tmp_path))

    assert out["success"] is True
    assert out["total"] == len(out["graphs"]) >= 10
    bundled = [g for g in out["graphs"] if g["scope"] == "bundled"]
    assert len(bundled) >= 10
    assert all(g["name"] for g in bundled), "a listed workflow with no name cannot be chosen"
    assert "feature-dev-graph" in {g["id"] for g in bundled}
    assert all(g["file"].endswith(".yaml") for g in bundled)


def test_a_project_workflow_is_listed_under_its_scope(tools, tmp_path):
    wf = tmp_path / ".claude" / "workflows"
    wf.mkdir(parents=True)
    (wf / "mine-graph.yaml").write_text(WAVE_GRAPH_YAML.replace('"Query Test"', '"Mine"'))

    out = tools["graph_list_available"](project_dir=str(tmp_path))

    mine = [g for g in out["graphs"] if g.get("name") == "Mine"]
    assert len(mine) == 1
    assert mine[0]["scope"] == "project"


def test_a_project_workflow_shadowing_a_bundled_one_is_listed_once_as_project(tools, tmp_path):
    wf = tmp_path / ".claude" / "workflows"
    wf.mkdir(parents=True)
    (wf / "feature-dev-graph.yaml").write_text(WAVE_GRAPH_YAML.replace('"Query Test"', '"Shadow"'))

    out = tools["graph_list_available"](project_dir=str(tmp_path))

    same_id = [g for g in out["graphs"] if g["id"] == "feature-dev-graph"]
    assert len(same_id) == 1, "a shadowed workflow was listed twice"
    assert same_id[0]["scope"] == "project"
    assert same_id[0]["name"] == "Shadow"


def test_a_yaml_that_is_not_a_graph_is_not_listed(tools, tmp_path):
    wf = tmp_path / ".claude" / "workflows"
    wf.mkdir(parents=True)
    (wf / "config.yaml").write_text("key: value\n")

    out = tools["graph_list_available"](project_dir=str(tmp_path))

    assert not any(g["id"] == "config" for g in out["graphs"])


# --- graph_deactivate ----------------------------------------------------------


def test_deactivate_ends_the_workflow_and_keeps_history(tools, tmp_path):
    p = _active(tmp_path)

    out = tools["graph_deactivate"](project_dir=p)

    assert out["success"] is True and out["deactivated"] is True
    assert "deactivated" in out["message"]
    state = load_graph_state(p)
    assert state.execution_path, "history was wiped; only the active pointer should clear"


def test_deactivating_twice_is_honest_about_the_second_time(tools, tmp_path):
    p = _active(tmp_path)
    tools["graph_deactivate"](project_dir=p)

    out = tools["graph_deactivate"](project_dir=p)

    assert out["deactivated"] is False
    assert "No active workflow" in out["message"]


# --- graph_validate ------------------------------------------------------------


def test_validate_with_no_graph_says_so(tools, tmp_path):
    out = tools["graph_validate"](project_dir=str(tmp_path))
    assert out["valid"] is False
    assert "No graph.yaml" in out["message"]


def test_validate_reports_a_well_formed_graph(tools, tmp_path):
    p = _active(tmp_path, DAG_GRAPH_YAML)

    out = tools["graph_validate"](project_dir=p)

    assert out["valid"] is True
    assert out["errors"] is None
    assert (out["node_count"], out["edge_count"]) == (3, 2)
    assert out["graph_name"] == "DAG Query Test"


def test_validate_reports_a_graph_that_does_not_parse(tools, tmp_path):
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text("nodes: [\n")

    out = tools["graph_validate"](project_dir=str(tmp_path))

    assert out["valid"] is False
    assert out["errors"]


# --- graph_visualize -----------------------------------------------------------


def test_visualize_renders_mermaid_with_every_node(tools, tmp_path):
    p = _active(tmp_path)

    out = tools["graph_visualize"](project_dir=p)

    assert out.get("error") is not True, out
    mermaid = out.get("mermaid") or out.get("diagram") or ""
    assert mermaid, out
    for node in ("start", "mid", "end"):
        assert node in mermaid


def test_visualize_without_a_workflow_is_an_error(tools, tmp_path):
    out = tools["graph_visualize"](project_dir=str(tmp_path))
    assert out.get("error") is True


# --- graph_override_max_visits -------------------------------------------------


def test_override_raises_the_limit_for_the_session(tools, tmp_path):
    p = _active(tmp_path)

    out = tools["graph_override_max_visits"](node_id="start", new_max=5, project_dir=p)

    assert out["success"] is True
    assert out["new_max_visits"] == 5
    assert "in-memory" in out["warning"]


def test_override_below_the_visits_already_made_is_refused(tools, tmp_path):
    p = _active(tmp_path)
    state = load_graph_state(p)
    state.current_nodes = ["mid"]
    save_graph_state(p, state)
    state = load_graph_state(p)
    visits = state.get_visit_count("start")

    out = tools["graph_override_max_visits"](node_id="start", new_max=visits, project_dir=p)

    assert out["error"] is True
    assert "must be greater" in out["message"]


def test_override_on_an_unknown_node_is_refused(tools, tmp_path):
    p = _active(tmp_path)
    out = tools["graph_override_max_visits"](node_id="nope", new_max=9, project_dir=p)
    assert out["error"] is True
    assert "not found" in out["message"]
