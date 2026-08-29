"""`vise runtime` is read-only and offline, and its exit code is load-bearing.

A plan with an unroutable task that exits 0 is a plan a script will happily act
on, which is the whole reason the planner reports problems instead of raising.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.cli.main import main

GRAPH = """
metadata:
  name: "oauth"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "backend-python-auth"
        name: "JWT middleware"
        role: "backend"
        criticality: "elevated"
        ownership:
          - "src/auth/**"
      - id: "migrate-users"
        name: "Add columns"
        role: "migration"
        ownership:
          - "src/auth/**"
edges: []
"""

BROKEN = GRAPH.replace('        role: "backend"\n', "")

WAVE_ONLY = """
metadata:
  name: "plain"
  version: "1.0"
nodes:
  - id: "start"
    name: "Start"
    is_start: true
    is_end: true
edges: []
"""


def _write(tmp_path: Path, text: str, name: str = "g-graph.yaml") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_plan_renders_waves_agents_and_reasons(tmp_path, capsys):
    assert main(["runtime", "plan", _write(tmp_path, GRAPH)]) == 0
    out = capsys.readouterr().out
    assert "wave 1" in out and "wave 2" in out, "conflicting owners cannot share a wave"
    assert "backend-python" in out
    assert "criticality elevated adds a rung" in out
    assert "total: 2 task(s)" in out


def test_plan_exits_non_zero_when_the_plan_has_problems(tmp_path, capsys):
    assert main(["runtime", "plan", _write(tmp_path, BROKEN)]) == 1
    assert "declares no role" in capsys.readouterr().out


def test_plan_emits_json(tmp_path, capsys):
    import json

    assert main(["runtime", "plan", _write(tmp_path, GRAPH), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["node"] == "implement"
    assert payload["task_count"] == 2


def test_plan_honours_a_cost_ceiling(tmp_path, capsys):
    assert main(["runtime", "plan", _write(tmp_path, GRAPH), "--max-cost", "0.5"]) == 1
    assert "budget" in capsys.readouterr().out


def test_plan_skips_tasks_already_completed(tmp_path, capsys):
    main(["runtime", "plan", _write(tmp_path, GRAPH),
          "--completed", "backend-python-auth"])
    assert "total: 1 task(s)" in capsys.readouterr().out


def test_plan_reports_a_graph_with_nothing_to_schedule(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "plan", _write(tmp_path, WAVE_ONLY)])
    assert exc.value.code == 2
    assert "no dag node with tasks" in capsys.readouterr().err


def test_plan_reports_a_missing_node(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "plan", _write(tmp_path, GRAPH), "--node", "ghost"])
    assert exc.value.code == 2
    assert "no node 'ghost'" in capsys.readouterr().err


def test_plan_reports_an_unreadable_graph(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "plan", str(tmp_path / "missing.yaml")])
    assert exc.value.code == 2
    assert "cannot read" in capsys.readouterr().err


def test_agents_lists_the_registry_and_names_ambiguous_roles(capsys):
    assert main(["runtime", "agents"]) == 0
    out = capsys.readouterr().out
    assert "reviewer" in out
    assert "backend" in out


def test_agents_reports_an_empty_directory(tmp_path, capsys):
    assert main(["runtime", "agents", "--dir", str(tmp_path)]) == 2
    assert "no agents found" in capsys.readouterr().err


def test_runtime_with_no_subcommand_exits_with_usage(capsys):
    with pytest.raises(SystemExit):
        main(["runtime"])
