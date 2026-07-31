"""Read-side graph tools: graph_status, graph_get_ready_tasks, graph_check_tool,
graph_check_phrase.

These are documented ``readOnlyHint: True`` — the property worth pinning above
any single field name is that a query never mutates persisted state (see
graph_status's own docstring history: it once resurrected a deactivated
workflow because a read path re-initialized state instead of just reading it).

Uses the same fake-MCP capture harness as test_graph_deactivate.py /
test_node_gate_traverse.py: the tools live inside a ``register_*(mcp)``
closure, so a fake decorator-collecting MCP is the only way to reach the real
registered callables without standing up a server.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_state import (
    get_graph_state_file,
    load_active_graph,
    load_graph_state,
    save_graph_state,
)

WAVE_GRAPH_YAML = """\
metadata:
  name: "Query Test"
nodes:
  - id: "start"
    name: "Start"
    is_start: true
    max_visits: 2
    tools_blocked: ["Write"]
    validators: []
    node_type: "wave"
  - id: "mid"
    name: "Mid"
  - id: "end"
    name: "End"
    is_end: true
edges:
  - id: "start-to-mid"
    from: "start"
    to: "mid"
    priority: 1
    condition:
      type: "tool"
      tool: "mcp__Context7__get-library-docs"
  - id: "start-to-end"
    from: "start"
    to: "end"
    priority: 2
    condition:
      type: "phrase"
      phrases: ["skip"]
  - id: "mid-to-end"
    from: "mid"
    to: "end"
    condition:
      type: "always"
"""

DAG_GRAPH_YAML = """\
metadata:
  name: "DAG Query Test"
nodes:
  - id: "plan"
    name: "Plan"
    is_start: true
  - id: "work"
    name: "Work"
    node_type: "dag"
    tasks:
      - id: "t1"
        name: "Task One"
      - id: "t2"
        name: "Task Two"
        dependencies: ["t1"]
      - id: "t3"
        name: "Task Three"
        dependencies: ["t2"]
  - id: "end"
    name: "End"
    is_end: true
edges:
  - id: "plan-to-work"
    from: "plan"
    to: "work"
  - id: "work-to-end"
    from: "work"
    to: "end"
"""


@pytest.fixture
def query_tools():
    """The real registered query tools, reached via the fake-MCP harness."""
    registered: dict = {}

    class _FakeMCP:
        def tool(self, *a, **kw):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    from vise.tools._graph_query import register_graph_query_tools

    register_graph_query_tools(_FakeMCP())
    return registered


def _wave_project(tmp_path: Path) -> str:
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(WAVE_GRAPH_YAML)
    load_active_graph(str(tmp_path))  # lazy-init onto the start node
    return str(tmp_path)


def _dag_project(tmp_path: Path) -> str:
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(DAG_GRAPH_YAML)
    load_active_graph(str(tmp_path))
    _move_to(str(tmp_path), "work")
    return str(tmp_path)


def _move_to(project_dir: str, node_id: str) -> None:
    state = load_graph_state(project_dir)
    state.current_nodes = [node_id]
    save_graph_state(project_dir, state)


def _raw_state_bytes(project_dir: str) -> bytes:
    return get_graph_state_file(project_dir).read_bytes()


# ---------------------------------------------------------------------------
# graph_status — no active workflow (project never touched at all, distinct
# from the deactivated-workflow case owned by test_graph_deactivate.py)
# ---------------------------------------------------------------------------

def test_graph_status_reports_inactive_when_no_graph_file_exists(query_tools, tmp_path):
    out = query_tools["graph_status"](project_dir=str(tmp_path))

    assert out["active"] is False
    assert not out.get("error")
    assert "graph_activate" in out["hint"]


# ---------------------------------------------------------------------------
# graph_status — active workflow, wave node
# ---------------------------------------------------------------------------

def test_graph_status_reports_current_node_and_edges(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_status"](project_dir=p)

    assert out["current_node"]["id"] == "start"
    assert out["current_node"]["tools_blocked"] == ["Write"]
    assert out["current_node"]["is_end"] is False
    edge_ids = [e["id"] for e in out["available_edges"]]
    assert edge_ids == ["start-to-mid", "start-to-end"], "edges must be priority-sorted"
    assert out["available_edges"][0]["condition_tool"] == "mcp__Context7__get-library-docs"
    assert out["available_edges"][1]["condition_phrases"] == ["skip"]


def test_graph_status_reports_visit_count_and_max_visits_honestly(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_status"](project_dir=p)

    assert out["current_node"]["visits"] == 1, "lazy-init counts the first visit"
    assert out["current_node"]["max_visits"] == 2


def test_graph_status_warns_at_max_visits(query_tools, tmp_path):
    p = _wave_project(tmp_path)
    state = load_graph_state(p)
    state.node_visits["start"] = 2
    save_graph_state(p, state)

    out = query_tools["graph_status"](project_dir=p)

    assert out["warnings"] is not None
    assert "BLOCKED" in out["warnings"][0]
    assert "2/2" in out["warnings"][0]


def test_graph_status_never_mutates_persisted_state(query_tools, tmp_path):
    """A read tool that silently advances or re-initializes a workflow is a
    bug this repo has shipped before — assert byte-identical state."""
    p = _wave_project(tmp_path)
    before = _raw_state_bytes(p)

    query_tools["graph_status"](project_dir=p)

    assert _raw_state_bytes(p) == before, "graph_status must not write state"


# ---------------------------------------------------------------------------
# graph_status — DAG node: completed / ready / blocked lists
# ---------------------------------------------------------------------------

def test_graph_status_dag_info_separates_completed_ready_and_blocked(query_tools, tmp_path):
    p = _dag_project(tmp_path)
    state = load_graph_state(p)
    state.completed_tasks["work:t1"] = {"completed_at": "now", "outputs": {}}
    save_graph_state(p, state)

    out = query_tools["graph_status"](project_dir=p)

    dag_info = out["dag_info"]
    assert dag_info["total_tasks"] == 3
    assert dag_info["completed"] == ["t1"]
    assert [t["id"] for t in dag_info["ready"]] == ["t2"], "t2's only dep is done"
    assert dag_info["blocked"] == ["t3"], "t3 depends on t2, which is not yet done"
    assert dag_info["is_complete"] is False


def test_graph_status_dag_info_is_none_for_a_wave_node(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_status"](project_dir=p)

    assert out["dag_info"] is None


# ---------------------------------------------------------------------------
# graph_get_ready_tasks
# ---------------------------------------------------------------------------

def test_graph_get_ready_tasks_returns_only_unblocked_tasks(query_tools, tmp_path):
    p = _dag_project(tmp_path)

    out = query_tools["graph_get_ready_tasks"](project_dir=p)

    assert [t["id"] for t in out["ready_tasks"]] == ["t1"]
    assert out["total_tasks"] == 3
    assert out["completed_count"] == 0
    assert out["is_dag_complete"] is False


def test_graph_get_ready_tasks_rejects_a_non_dag_current_node(query_tools, tmp_path):
    p = _wave_project(tmp_path)  # current node "start" is node_type "wave"

    out = query_tools["graph_get_ready_tasks"](project_dir=p)

    assert out["error"] is True
    assert "not a DAG node" in out["message"]


def test_graph_get_ready_tasks_reports_no_active_workflow_as_an_error_dict(
    query_tools, tmp_path
):
    """Unlike graph_status, this tool has no dedicated "inactive" shape — it
    folds NoActiveWorkflowError into its generic error branch."""
    out = query_tools["graph_get_ready_tasks"](project_dir=str(tmp_path))

    assert out["error"] is True
    assert "graph.yaml" in out["message"]


# ---------------------------------------------------------------------------
# graph_check_tool
# ---------------------------------------------------------------------------

def test_graph_check_tool_matches_the_declared_edge(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_check_tool"](
        mcp_name="Context7", tool_name="get-library-docs", project_dir=p
    )

    assert out["matched"] is True
    assert out["matching_edges"][0]["id"] == "start-to-mid"
    assert out["recommended_edge"] == "start-to-mid"


def test_graph_check_tool_reports_no_match_without_erroring(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_check_tool"](
        mcp_name="unrelated", tool_name="noop", project_dir=p
    )

    assert out["matched"] is False
    assert out["current_node"] == "start"


def test_graph_check_tool_never_mutates_persisted_state(query_tools, tmp_path):
    p = _wave_project(tmp_path)
    before = _raw_state_bytes(p)

    query_tools["graph_check_tool"](
        mcp_name="Context7", tool_name="get-library-docs", project_dir=p
    )

    assert _raw_state_bytes(p) == before


# ---------------------------------------------------------------------------
# graph_check_phrase
# ---------------------------------------------------------------------------

def test_graph_check_phrase_matches_a_declared_phrase(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_check_phrase"](text="let's just skip this", project_dir=p)

    assert out["matched"] is True
    assert out["matched_phrase"] == "skip"
    assert out["matching_edges"][0]["id"] == "start-to-end"


def test_graph_check_phrase_lists_available_phrases_on_no_match(query_tools, tmp_path):
    p = _wave_project(tmp_path)

    out = query_tools["graph_check_phrase"](text="nothing relevant here", project_dir=p)

    assert out["matched"] is False
    assert out["available_phrases"] == ["skip"]


def test_graph_check_phrase_never_mutates_persisted_state(query_tools, tmp_path):
    p = _wave_project(tmp_path)
    before = _raw_state_bytes(p)

    query_tools["graph_check_phrase"](text="skip", project_dir=p)

    assert _raw_state_bytes(p) == before
