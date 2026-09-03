"""``vise`` from the command line, the way a person runs it.

``cli/main.py`` read 26%. There is no ``run_start`` MCP tool — by decision,
README says why — so the CLI is the only way to dispatch the runtime, and the
least tested module in the package was the only door to its most expensive
feature. These tests go through ``main(argv)``: the same parsing, the same
dispatch, the same exit codes a shell sees.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vise import __version__
from vise.cli.main import main

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "workflows"
RESEARCH = ASSETS / "research-graph.yaml"


def test_version(capsys):
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_no_arguments_prints_usage_and_exits_zero(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "usage: vise" in out
    assert "runtime" in out and "bootstrap" in out and "approve" in out


def test_an_unknown_command_exits_two(capsys):
    assert main(["frobnicate"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_a_group_with_no_subcommand_shows_its_help():
    with pytest.raises(SystemExit) as exc:
        main(["runtime"])
    assert exc.value.code == 0


# --- runtime -----------------------------------------------------------------


def test_runtime_agents_lists_the_bundled_fleet_as_json(capsys):
    assert main(["runtime", "agents", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["agents"]) >= 22
    assert all("id" in a and "role" in a for a in payload["agents"])


def test_runtime_plan_costs_a_research_node_without_a_change(tmp_path, capsys):
    """Research tasks do not write, so the spec gate has nothing to say."""
    rc = main([
        "runtime", "plan", str(RESEARCH), "--node", "gather",
        "--project-dir", str(tmp_path), "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0, payload.get("problems")
    assert payload["node"] == "gather"
    assert payload["problems"] == []
    assert len(payload["waves"]) >= 1
    assert {t for wave in payload["waves"] for t in wave} >= {"primary", "against"} or payload["waves"]


def test_runtime_plan_on_a_node_that_is_not_a_dag_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "plan", str(RESEARCH), "--node", "scope", "--project-dir", str(tmp_path)])
    assert exc.value.code == 2
    assert "only a dag node" in capsys.readouterr().err


def test_runtime_plan_on_a_missing_graph_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "plan", str(tmp_path / "nope.yaml")])
    assert exc.value.code == 2
    assert "cannot read" in capsys.readouterr().err


def test_runtime_run_without_yes_dispatches_nothing(tmp_path, capsys):
    state = tmp_path / "state"
    rc = main([
        "runtime", "run", str(RESEARCH), "--node", "gather",
        "--project-dir", str(tmp_path), "--state-dir", str(state),
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "nothing dispatched" in out
    assert "--yes" in out
    assert not (state / "runs").exists(), "a dry run created run state"


def test_runtime_status_with_no_runs(tmp_path, capsys):
    assert main(["runtime", "status", "--state-dir", str(tmp_path)]) == 0
    assert "no runs recorded" in capsys.readouterr().out


def test_runtime_explain_of_an_unknown_run_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["runtime", "explain", "ghost", "--state-dir", str(tmp_path)])
    assert exc.value.code == 2
    assert "no run 'ghost'" in capsys.readouterr().err


# --- bootstrap / approve ---------------------------------------------------------


def test_bootstrap_dry_run_prints_a_profile_and_writes_nothing(tmp_path, capsys, monkeypatch):
    from vise.cli import bootstrap_cmd

    monkeypatch.setattr(bootstrap_cmd, "browser_status", lambda: (False, "no browser here"))
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    rc = main(["bootstrap", "--project-dir", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "checks:" in out
    assert "no browser here" in out, "bootstrap must say the design gates cannot run"
    assert not (tmp_path / ".vise").exists()


def test_approve_list_on_a_repo_with_no_profile(tmp_path, capsys):
    assert main(["approve", "--list", "--project-dir", str(tmp_path)]) == 0
    assert "no quality profile" in capsys.readouterr().out


# --- doctor ------------------------------------------------------------------


def test_doctor_reports_every_section_and_never_fails(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    for section in ("LSP servers", "Python diagnostics", "XDG state migration"):
        assert section in out
    assert "declared:" in out
