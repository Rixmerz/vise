"""The spec gate on the execution plane — see openspec/changes/runtime-spec-gate.

vise's node gate makes the spec phase impossible to talk past, and
`skills/orchestration/SKILL.md` says why delegation is not an escape hatch: a
subagent hits the same gate, because it is a node gate rather than a prompt.

The agent runtime broke that claim. A `dag` node's tasks never traverse the
graph — the scheduler turns them straight into subprocesses — so they reach no
node gate at all. Every test here is a claim about the gate that closes that
door, and the last one is the claim that matters most: a blocked run spends
nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.contracts import RunBudget, RunSpec
from vise.runtime.planner import plan
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.spec_gate import OVERRIDE_ENV, check
from vise.runtime.state import TaskState
from vise.runtime.worker import MockWorker

WELL_FORMED = """## ADDED Requirements

### Requirement: The thing works

It SHALL work.

#### Scenario: It works
- **WHEN** asked
- **THEN** it works
"""


def _project(tmp_path: Path, *, change: str = "add-auth", spec: str | None = WELL_FORMED,
             proposal: bool = True, tasks: str | None = None) -> Path:
    """A project tree with an OpenSpec change in whatever shape the test needs."""
    root = tmp_path / "proj"
    change_dir = root / "openspec" / "changes" / change
    change_dir.mkdir(parents=True)
    if proposal:
        (change_dir / "proposal.md").write_text("## Why\n\nbecause\n", encoding="utf-8")
    if spec is not None:
        cap = change_dir / "specs" / "thing"
        cap.mkdir(parents=True)
        (cap / "spec.md").write_text(spec, encoding="utf-8")
    if tasks is not None:
        (change_dir / "tasks.md").write_text(tasks, encoding="utf-8")
    return root


# --- the gate itself ------------------------------------------------------


def test_a_project_with_no_openspec_root_is_blocked(tmp_path: Path):
    verdict = check(tmp_path)
    assert verdict.ok is False
    assert "openspec init" in verdict.reason


def test_a_root_with_no_active_change_is_blocked(tmp_path: Path):
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    verdict = check(tmp_path)
    assert verdict.ok is False
    assert "openspec new change" in verdict.reason


def test_a_change_with_no_delta_files_is_blocked(tmp_path: Path):
    verdict = check(_project(tmp_path, spec=None))
    assert verdict.ok is False
    assert "no specs/**/*.md delta" in verdict.reason
    assert "add-auth" in verdict.reason


def test_a_delta_with_no_header_is_blocked_and_names_the_header(tmp_path: Path):
    body = "### Requirement: Nope\n\n#### Scenario: s\n- **WHEN** x\n- **THEN** y\n"
    verdict = check(_project(tmp_path, spec=body))
    assert verdict.ok is False
    assert "ADDED" in verdict.reason


def test_a_requirement_with_no_scenario_is_blocked_and_named(tmp_path: Path):
    """The single most common way a hand-written change fails validation."""
    body = "## ADDED Requirements\n\n### Requirement: Solo\n\nIt SHALL be lonely.\n"
    verdict = check(_project(tmp_path, spec=body))
    assert verdict.ok is False
    assert "Solo" in verdict.reason
    assert "Scenario" in verdict.reason


def test_a_change_with_no_proposal_is_blocked(tmp_path: Path):
    verdict = check(_project(tmp_path, proposal=False))
    assert verdict.ok is False
    assert "proposal.md" in verdict.reason


def test_a_well_formed_change_admits_the_run(tmp_path: Path):
    verdict = check(_project(tmp_path))
    assert verdict.ok is True
    assert verdict.change == "add-auth"
    assert verdict.overridden is False


def test_an_unfinished_checklist_still_admits_the_run(tmp_path: Path):
    """The run is what ticks those boxes. Requiring them first would gate the
    work on its own output and no run could ever start."""
    todo = "- [ ] 1.1 one\n- [ ] 1.2 two\n- [x] 1.3 three\n"
    verdict = check(_project(tmp_path, tasks=todo))
    assert verdict.ok is True


def test_a_read_only_run_is_not_gated(tmp_path: Path):
    verdict = check(tmp_path, writes=False)
    assert verdict.ok is True
    assert "nothing to specify" in verdict.reason


# --- pinning --------------------------------------------------------------


def test_the_pinned_change_is_the_one_checked(tmp_path: Path):
    root = _project(tmp_path, change="add-billing")
    verdict = check(root, change="add-auth")
    assert verdict.ok is False
    assert "add-auth" in verdict.reason
    assert "add-billing" in verdict.reason  # what is actually there


def test_a_pinned_change_that_is_present_and_well_formed_passes(tmp_path: Path):
    verdict = check(_project(tmp_path, change="add-billing"), change="add-billing")
    assert verdict.ok is True
    assert verdict.change == "add-billing"


def test_an_unpinned_gate_accepts_any_well_formed_change(tmp_path: Path):
    root = _project(tmp_path, change="good")
    bad = root / "openspec" / "changes" / "bad"
    bad.mkdir()
    (bad / "proposal.md").write_text("## Why\n", encoding="utf-8")
    verdict = check(root)
    assert verdict.ok is True
    assert verdict.change == "good"


# --- the override ---------------------------------------------------------


def test_the_override_proceeds_but_never_reports_itself_as_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An audit that cannot tell a met gate from a bypassed one is not an audit."""
    monkeypatch.setenv(OVERRIDE_ENV, "1")
    verdict = check(tmp_path)
    assert verdict.ok is True
    assert verdict.overridden is True
    assert "openspec init" in verdict.reason
    assert "overridden" in verdict.render()


def test_any_other_value_of_the_override_is_not_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(OVERRIDE_ENV, "yes")
    assert check(tmp_path).ok is False


# --- degradation ----------------------------------------------------------


def test_a_changes_path_that_is_a_file_blocks_without_raising(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "openspec").mkdir(parents=True)
    (root / "openspec" / "changes").write_text("not a directory\n", encoding="utf-8")
    verdict = check(root)
    assert verdict.ok is False
    assert "no active change" in verdict.reason


# --- through the planner and the scheduler --------------------------------


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.agents["backend-python"] = AgentSpec(
        id="backend-python", role="backend", description="d", model="sonnet",
        capabilities=("backend", "python"),
    )
    return reg


def test_the_plan_reports_the_block_so_it_costs_nothing_to_discover(tmp_path: Path):
    tasks = [Task(id="a", name="a", role="backend", ownership=["src/**"])]
    result = plan(tasks, registry=_registry(), project_dir=str(tmp_path))
    assert any("spec gate" in p for p in result.problems)


def test_a_plan_for_a_read_only_run_is_not_gated(tmp_path: Path):
    tasks = [Task(id="a", name="a", role="backend", ownership=[], writes=False)]
    result = plan(tasks, registry=_registry(), project_dir=str(tmp_path))
    assert not any("spec gate" in p for p in result.problems)


def test_a_blocked_run_dispatches_nothing_and_spends_nothing(tmp_path: Path):
    """The claim the whole change rests on. A gate that blocks after the third
    worker has run is an audit, not a gate."""
    worker = MockWorker()
    tasks = [
        Task(id="a", name="a", role="backend", ownership=["src/a/**"]),
        Task(id="b", name="b", role="backend", ownership=["src/b/**"]),
    ]
    spec = RunSpec(run_id="r1", goal="g", project_dir=str(tmp_path),
                   budget=RunBudget(max_parallel=2))
    state = Scheduler(
        worker=worker, registry=_registry(),
        # The real check, not the suite-wide open one — this test is about it.
        config=SchedulerConfig(spec_gate=check),
    ).run(spec, tasks)

    assert worker.briefs == []
    assert state.ledger.spent.cost_usd == 0.0
    assert all(r.state is TaskState.BLOCKED for r in state.tasks.values())
    assert any("openspec init" in (r.note or "") for r in state.tasks.values())
    assert [e["kind"] for e in state.events if e["kind"].startswith("spec_gate")] \
        == ["spec_gate_blocked"]


def test_a_well_formed_project_dispatches_and_records_the_change(tmp_path: Path):
    root = _project(tmp_path)
    tasks = [Task(id="a", name="a", role="backend", ownership=["src/**"])]
    spec = RunSpec(run_id="r1", goal="g", project_dir=str(root))
    state = Scheduler(
        worker=MockWorker(), registry=_registry(),
        config=SchedulerConfig(spec_gate=check, verify=False),
    ).run(spec, tasks)

    passed = [e for e in state.events if e["kind"] == "spec_gate_passed"]
    assert passed and passed[0]["change"] == "add-auth"
    assert state.tasks["a"].state is TaskState.SUCCEEDED


def test_a_read_only_run_is_never_blocked_by_the_scheduler(tmp_path: Path):
    tasks = [Task(id="a", name="a", role="backend", ownership=[], writes=False)]
    spec = RunSpec(run_id="r1", goal="g", project_dir=str(tmp_path))
    state = Scheduler(
        worker=MockWorker(), registry=_registry(),
        config=SchedulerConfig(spec_gate=check, verify=False),
    ).run(spec, tasks)
    assert state.tasks["a"].state is TaskState.SUCCEEDED
