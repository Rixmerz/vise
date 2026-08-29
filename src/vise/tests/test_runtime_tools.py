"""The runtime's MCP surface, and the one tool it deliberately does not expose.

`run_start` is absent on purpose: this server runs inside a Claude Code session,
so a dispatch tool here would have the session spawning sessions through the one
component that cannot call another server's tools. That absence is a design
decision, so it gets a test.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import RunBudget, RunSpec
from vise.runtime.scheduler import Scheduler
from vise.runtime.worker import MockWorker


class _FakeMCP:
    """Captures the functions a register_* call decorates."""

    def __init__(self):
        self.tools: dict[str, object] = {}

    def tool(self, *_a, **_k):
        def wrap(fn):
            self.tools[fn.__name__] = fn
            return fn
        return wrap


@pytest.fixture
def tools():
    from vise.tools.runtime import register_runtime

    fake = _FakeMCP()
    register_runtime(fake)
    return fake.tools


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    spec = RunSpec(run_id="run-abc", goal="ship oauth", project_dir=str(tmp_path),
                   budget=RunBudget(max_cost_usd=10))
    tasks = [Task(id="backend-python-auth", name="auth", role="backend",
                  ownership=["src/auth/**"])]
    Scheduler(worker=MockWorker(), state_root=root).run(spec, tasks)
    return root


GRAPH = """
metadata:
  name: "g"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "backend-python-auth"
        name: "auth"
        role: "backend"
        ownership:
          - "src/auth/**"
edges: []
"""


def test_there_is_no_run_start_tool(tools):
    """Dispatch belongs to the CLI, where the operator spends the money."""
    assert "run_start" not in tools
    assert not any(name.endswith("_dispatch") for name in tools)


def test_the_family_registers_the_tools_it_documents(tools):
    assert set(tools) == {
        "agent_list", "run_plan", "run_list", "run_status", "task_list",
        "run_explain", "run_budget", "run_cancel",
    }


def test_agent_list_reports_the_registry_and_its_ambiguous_roles(tools):
    out = tools["agent_list"]()
    assert out["count"] >= 20
    assert "backend" in out["ambiguous_roles"]
    assert "review" not in out["ambiguous_roles"]


def test_run_plan_costs_a_node_without_running_it(tmp_path, tools):
    path = tmp_path / "g-graph.yaml"
    path.write_text(GRAPH, encoding="utf-8")
    out = tools["run_plan"](graph_path=str(path))
    assert out["node"] == "implement"
    assert out["task_count"] == 1
    assert out["estimated_cost_usd"] > 0
    assert "wave 1" in out["rendered"]


def test_run_plan_reports_an_unreadable_graph_rather_than_raising(tmp_path, tools):
    assert "error" in tools["run_plan"](graph_path=str(tmp_path / "gone.yaml"))


def test_run_plan_reports_a_graph_with_nothing_to_schedule(tmp_path, tools):
    path = tmp_path / "empty-graph.yaml"
    path.write_text(
        'metadata:\n  name: "e"\n  version: "1.0"\nnodes:\n  - id: "n"\n'
        '    name: "N"\n    is_start: true\n    is_end: true\nedges: []\n',
        encoding="utf-8",
    )
    assert "error" in tools["run_plan"](graph_path=str(path))


def test_run_list_reports_recorded_runs(seeded, tools):
    out = tools["run_list"](state_dir=str(seeded))
    assert out["count"] == 1
    assert out["runs"][0]["run_id"] == "run-abc"
    assert out["runs"][0]["succeeded"] is True


def test_run_list_is_empty_before_anything_runs(tmp_path, tools):
    assert tools["run_list"](state_dir=str(tmp_path))["count"] == 0


def test_run_status_reports_every_task(seeded, tools):
    out = tools["run_status"](run_id="run-abc", state_dir=str(seeded))
    assert out["succeeded"] is True
    assert "backend-python-auth" in out["tasks"]


def test_task_list_names_the_agent_and_model_that_ran_each_task(seeded, tools):
    rows = tools["task_list"](run_id="run-abc", state_dir=str(seeded))["tasks"]
    assert rows[0]["agent_id"] == "backend-python"
    assert rows[0]["model"] == "sonnet"


def test_run_explain_returns_the_decisions_in_order(seeded, tools):
    events = tools["run_explain"](run_id="run-abc", state_dir=str(seeded))["events"]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"


def test_run_explain_marks_a_truncated_log(seeded, tools):
    out = tools["run_explain"](run_id="run-abc", limit=1, state_dir=str(seeded))
    assert out["truncated"] is True
    assert len(out["events"]) == 1


def test_run_budget_names_the_ceilings_nobody_set(seeded, tools):
    out = tools["run_budget"](run_id="run-abc", state_dir=str(seeded))
    assert "max_workers" in out["unset_ceilings"]


def test_run_cancel_writes_a_sentinel_and_says_when_it_is_pointless(seeded, tools):
    out = tools["run_cancel"](run_id="run-abc", state_dir=str(seeded))
    assert Path(out["sentinel"]).exists()
    assert out["already_finished"] is True
    assert "will not do anything" in out["note"]


def test_every_reader_reports_an_unknown_run_rather_than_raising(tmp_path, tools):
    for name in ("run_status", "task_list", "run_explain", "run_budget"):
        out = tools[name](run_id="nope", state_dir=str(tmp_path))
        assert "error" in out and "run_list" in out["hint"]


def test_the_family_is_registered_on_the_real_server():
    from vise.server import mcp
    from vise.tools.bootstrap import register_all

    register_all(mcp)
    names = {getattr(t, "name", str(t)) for t in asyncio.run(mcp._list_tools())}
    assert {"agent_list", "run_plan", "run_status", "task_list"} <= names
