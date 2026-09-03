"""The four mutation tools, called the way an agent calls them.

``_graph_mutation.py`` read 21% while the engines under it read over 80%: the
surface agents actually touch was the least tested one. Each tool here is
reached through the registered callable with its real parameter names, and
each assertion is about the dict an agent gets back — the same harness
``test_node_gate_traverse.py`` uses for ``graph_traverse``.
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

    from vise.tools._graph_mutation import register_graph_mutation_tools

    register_graph_mutation_tools(_FakeMCP())
    return registered


def _project(tmp_path: Path, yaml: str, at: str | None = None) -> str:
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(yaml)
    load_active_graph(str(tmp_path))
    if at:
        state = load_graph_state(str(tmp_path))
        state.current_nodes = [at]
        save_graph_state(str(tmp_path), state)
    return str(tmp_path)


# --- graph_task_complete ---------------------------------------------------


def test_completing_a_task_unblocks_exactly_its_dependents(tools, tmp_path):
    p = _project(tmp_path, DAG_GRAPH_YAML, at="work")

    out = tools["graph_task_complete"](task_id="t1", project_dir=p)

    assert out["success"] is True
    assert out["completed"] == "t1"
    assert [t["id"] for t in out["newly_ready"]] == ["t2"]
    assert out["still_ready"] == []
    assert out["is_dag_complete"] is False
    assert (out["completed_count"], out["total_tasks"], out["remaining"]) == (1, 3, 2)


def test_outputs_are_kept_for_the_dependents(tools, tmp_path):
    p = _project(tmp_path, DAG_GRAPH_YAML, at="work")

    tools["graph_task_complete"](task_id="t1", outputs={"port": "8080"}, project_dir=p)

    state = load_graph_state(p)
    assert state.is_task_complete("work", "t1")


def test_the_last_task_completes_the_dag(tools, tmp_path):
    p = _project(tmp_path, DAG_GRAPH_YAML, at="work")
    for task in ("t1", "t2"):
        tools["graph_task_complete"](task_id=task, project_dir=p)

    out = tools["graph_task_complete"](task_id="t3", project_dir=p)

    assert out["is_dag_complete"] is True
    assert out["remaining"] == 0
    assert out["ready"] == []


def test_completing_a_task_twice_is_an_error_not_a_second_completion(tools, tmp_path):
    p = _project(tmp_path, DAG_GRAPH_YAML, at="work")
    tools["graph_task_complete"](task_id="t1", project_dir=p)

    out = tools["graph_task_complete"](task_id="t1", project_dir=p)

    assert out["error"] is True
    assert "already complete" in out["message"]


def test_an_unknown_task_names_the_ones_that_exist(tools, tmp_path):
    p = _project(tmp_path, DAG_GRAPH_YAML, at="work")

    out = tools["graph_task_complete"](task_id="nope", project_dir=p)

    assert out["error"] is True
    assert set(out["available_tasks"]) == {"t1", "t2", "t3"}


def test_completing_a_task_outside_a_dag_node_is_refused(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML)

    out = tools["graph_task_complete"](task_id="t1", project_dir=p)

    assert out["error"] is True
    assert "not a DAG node" in out["message"]


def test_no_active_workflow_is_an_error_for_every_mutation(tools, tmp_path):
    p = str(tmp_path)
    for name, kwargs in (
        ("graph_task_complete", {"task_id": "t1"}),
        ("graph_reset", {}),
        ("graph_set_node", {"node_id": "end"}),
        ("graph_record_output", {"key": "k", "value": "v"}),
    ):
        out = tools[name](project_dir=p, **kwargs)
        assert out.get("error") is True, (name, out)
        assert out["project_dir"] == p


# --- graph_reset -------------------------------------------------------------


def test_reset_returns_to_the_start_node_and_clears_visits(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML, at="mid")

    out = tools["graph_reset"](project_dir=p)

    assert out["success"] is True
    assert out["current_node"]["id"] == "start"
    state = load_graph_state(p)
    assert state.get_current_node() == "start"
    assert state.get_visit_count("mid") == 0


# --- graph_set_node ----------------------------------------------------------


def test_set_node_jumps_and_hands_back_that_nodes_prompt(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML)

    out = tools["graph_set_node"](node_id="end", project_dir=p)

    assert out["success"] is True
    assert out["current_node"]["id"] == "end"
    assert out["current_node"]["is_end"] is True
    assert "prompt_injection" in out
    assert load_graph_state(p).get_current_node() == "end"


def test_set_node_records_the_jump_as_a_transition(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML)

    tools["graph_set_node"](node_id="mid", project_dir=p)

    state = load_graph_state(p)
    last = state.execution_path[-1]
    assert last.to_node == "mid"
    assert last.from_node == "start"
    assert "Admin jump" in last.reason


def test_set_node_to_a_node_that_does_not_exist_lists_the_real_ones(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML)

    out = tools["graph_set_node"](node_id="nowhere", project_dir=p)

    assert out["error"] is True
    assert set(out["available_nodes"]) == {"start", "mid", "end"}
    assert load_graph_state(p).get_current_node() == "start", "a failed jump moved the state"


# --- graph_record_output -----------------------------------------------------


def test_record_output_lands_on_the_current_path_entry(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML)

    out = tools["graph_record_output"](key="next_migration", value="000028", project_dir=p)

    assert out["success"] is True
    assert out["current_outputs"] == {"next_migration": "000028"}
    assert out["node"] == "start"
    assert load_graph_state(p).execution_path[-1].outputs == {"next_migration": "000028"}


def test_a_second_output_is_added_not_overwritten(tools, tmp_path):
    p = _project(tmp_path, WAVE_GRAPH_YAML)
    tools["graph_record_output"](key="a", value="1", project_dir=p)

    out = tools["graph_record_output"](key="b", value="2", project_dir=p)

    assert out["current_outputs"] == {"a": "1", "b": "2"}
