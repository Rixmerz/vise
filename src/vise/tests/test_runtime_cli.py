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


# --- run, status, explain, budget, cancel --------------------------------

RUNNABLE = """
metadata:
  name: "runnable"
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
        ownership:
          - "src/auth/**"
edges: []
"""


def test_run_prints_the_plan_and_dispatches_nothing_without_yes(tmp_path, capsys):
    """The moment to refuse a four-dollar run is before it starts."""
    assert main(["runtime", "run", _write(tmp_path, RUNNABLE),
                 "--state-dir", str(tmp_path / "state")]) == 0
    out = capsys.readouterr().out
    assert "Re-run with --yes" in out
    assert not (tmp_path / "state" / "runs").exists()


def test_run_refuses_a_plan_with_problems(tmp_path, capsys):
    assert main(["runtime", "run", _write(tmp_path, BROKEN),
                 "--state-dir", str(tmp_path / "state")]) == 1
    assert "refusing to run a plan with problems" in capsys.readouterr().err


def _seeded_run(tmp_path):
    """A finished run on disk, produced by the scheduler with a mock worker."""
    from vise.engines.graph_engine import Task
    from vise.runtime.contracts import RunBudget, RunSpec
    from vise.runtime.scheduler import Scheduler
    from vise.runtime.worker import MockWorker

    root = tmp_path / "state"
    spec = RunSpec(run_id="run-abc", goal="ship oauth",
                   project_dir=str(tmp_path), budget=RunBudget(max_cost_usd=10))
    # `backend-python-auth`, not `auth`: twelve bundled agents take the backend
    # role, so a task that names no language is unroutable by design. Naming one
    # is how a real workflow reaches an agent.
    tasks = [Task(id="backend-python-auth", name="auth", role="backend",
                  ownership=["src/auth/**"])]
    Scheduler(worker=MockWorker(), state_root=root).run(spec, tasks)
    return root


def test_status_reads_a_finished_run_back(tmp_path, capsys):
    root = _seeded_run(tmp_path)
    assert main(["runtime", "status", "run-abc", "--state-dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "ship oauth" in out and "backend-python-auth" in out and "succeeded" in out
    assert "backend-python" in out, "the status line names the agent that ran it"


def test_status_with_no_run_id_lists_recent_runs(tmp_path, capsys):
    root = _seeded_run(tmp_path)
    assert main(["runtime", "status", "--state-dir", str(root)]) == 0
    assert "run-abc" in capsys.readouterr().out


def test_status_says_so_when_nothing_has_run(tmp_path, capsys):
    assert main(["runtime", "status", "--state-dir", str(tmp_path)]) == 0
    assert "no runs recorded" in capsys.readouterr().out


def test_explain_narrates_every_scheduler_decision(tmp_path, capsys):
    root = _seeded_run(tmp_path)
    assert main(["runtime", "explain", "run-abc", "--state-dir", str(root)]) == 0
    out = capsys.readouterr().out
    for kind in ("run_started", "dispatched", "collected", "run_finished"):
        assert kind in out


def test_explain_emits_json(tmp_path, capsys):
    import json as _json

    root = _seeded_run(tmp_path)
    main(["runtime", "explain", "run-abc", "--state-dir", str(root), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-abc"
    assert payload["events"]


def test_budget_reports_per_task_and_names_unset_ceilings(tmp_path, capsys):
    root = _seeded_run(tmp_path)
    assert main(["runtime", "budget", "run-abc", "--state-dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "per task" in out
    assert "unset ceilings" in out, "an unset ceiling is reported, not called unlimited"


def test_reading_an_unknown_run_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "explain", "nope", "--state-dir", str(tmp_path)])
    assert exc.value.code == 2
    assert "no run 'nope'" in capsys.readouterr().err


def test_cancel_writes_a_sentinel_a_running_scheduler_reads(tmp_path, capsys):
    from vise.engines.graph_engine import Task
    from vise.runtime.contracts import RunSpec
    from vise.runtime.scheduler import Scheduler
    from vise.runtime.state import cancel_requested
    from vise.runtime.worker import MockWorker

    root = tmp_path / "state"
    assert main(["runtime", "cancel", "run-xyz", "--state-dir", str(root)]) == 0
    assert cancel_requested(root, "run-xyz")

    spec = RunSpec(run_id="run-xyz", goal="g", project_dir=str(tmp_path))
    tasks = [Task(id="a", name="a", role="backend", ownership=["src/**"])]
    state = Scheduler(worker=MockWorker(), state_root=root).run(spec, tasks)
    assert state.cancelled
    assert "vise runtime cancel" in state.cancel_reason
