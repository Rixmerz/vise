"""Running a composed plan as the continuation of the run that produced it.

Every step of the loop existed and the chain still broke at one point: running
the composed graph meant `vise runtime run`, which starts from nothing. The
brief's first line — *"already done — do not plan these again"* — was addressed
to a person, and a person was the only thing that could act on it.

The cost that matters is not the duplicated work. It is that `--max-cost` on a
follow-up bounded the follow-up, so a goal pursued across four runs was under
budget in each and unbounded overall. `resume` already refuses that reasoning
for one run; these tests hold it for a chain.

What deliberately does NOT carry is the succeeded records. Identity is exactly
what a new plan may have changed, and a composed `cli-python` with new
ownership is not the `cli-python` that failed. Declaring a task is how a plan
asks for it; `--skip-done` is for the caller who knows better.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from vise.runtime.contracts import (
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.state import RunState

GRAPH = """
metadata:
  name: "followup"
  version: "1.0"
nodes:
  - id: "rebuild"
    name: "Rebuild"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "alpha"
        name: "alpha python work"
        role: "backend"
        ownership: ["src/alpha"]
      - id: "beta"
        name: "beta python work"
        role: "backend"
        dependencies: ["alpha"]
        ownership: ["src/beta"]
edges: []
"""


@pytest.fixture
def project(tmp_path) -> Path:
    """A tree the spec gate will let a writing run dispatch against."""
    repo = tmp_path / "project"
    (repo / "src").mkdir(parents=True)
    change = repo / "openspec" / "changes" / "followup"
    (change / "specs" / "thing").mkdir(parents=True)
    (change / "proposal.md").write_text(
        "# Follow-up\n\n## Why\nTo exercise continue.\n\n## What Changes\n- A thing.\n",
        encoding="utf-8")
    (change / "specs" / "thing" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: The thing exists\n"
        "The system SHALL provide a thing.\n\n"
        "#### Scenario: asking for the thing\n- **WHEN** someone asks\n"
        "- **THEN** it answers\n",
        encoding="utf-8")
    return repo


@pytest.fixture
def graph(tmp_path) -> Path:
    path = tmp_path / "followup-graph.yaml"
    path.write_text(GRAPH)
    return path


def _prior(project, *, spent: float = 3.55, done=("alpha",)) -> RunState:
    """A stopped run that paid for `done` and spent `spent`."""
    spec = RunSpec(
        run_id="prior", goal="build the thing", project_dir=str(project),
        graph_name="original", node_id="build", budget=RunBudget(max_parallel=4),
    )
    state = RunState.for_tasks(spec, ["alpha", "gamma"])
    for task_id in done:
        state.finish(task_id, TaskResult(
            task_id=task_id, verdict=Verdict.PASS, summary="done",
            usage=Usage(cost_usd=spent, tokens_in=1, tokens_out=1),
        ))
        state.tasks[task_id].state = TaskState.SUCCEEDED
    state.stop_for_human("a person must decide")
    return state


def _args(tmp_path, graph, **over) -> argparse.Namespace:
    base = dict(
        run_id="prior", graph=str(graph), node=None, goal=None,
        max_cost=0.0, max_parallel=4, skip_done=False,
        project_dir=None, permission_mode=None, isolate=False, no_verify=False,
        change=None, state_dir=str(tmp_path / "state"), new_run_id=None, yes=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _continue(state, tmp_path, graph, **over) -> tuple[int, str]:
    from vise.cli.runtime_cmd import _cmd_continue

    root = tmp_path / "state"
    state.save(root)
    return _cmd_continue(_args(tmp_path, graph, **over))


# --- the ceiling bounds the chain -----------------------------------------


def test_the_prior_spend_counts_against_the_ceiling(project, tmp_path, graph, capsys):
    """A ceiling that resets between links is not a ceiling."""
    rc = _continue(_prior(project, spent=3.55), tmp_path, graph, max_cost=4.0)

    out = capsys.readouterr()
    assert rc == 1, "a chain already over budget must not be dispatched"
    assert "does not fit the remaining run budget" in out.out
    assert "inherited spend: $3.55" in out.out


def test_a_ceiling_that_fits_the_whole_chain_plans_cleanly(project, tmp_path, graph, capsys):
    rc = _continue(_prior(project, spent=1.00), tmp_path, graph, max_cost=20.0)

    out = capsys.readouterr().out
    assert rc == 0
    assert "problems" not in out
    assert "on top of $1.00" in out


def test_without_a_ceiling_the_spend_is_still_reported(project, tmp_path, graph, capsys):
    rc = _continue(_prior(project, spent=2.00), tmp_path, graph)

    assert rc == 0
    assert "inherited spend: $2.00" in capsys.readouterr().out


# --- what runs and what does not ------------------------------------------


def test_a_task_the_plan_declares_runs_even_if_its_id_succeeded_before(
    project, tmp_path, graph, capsys
):
    """Declaring a task is how a plan asks for it.

    Nothing here can tell whether two tasks sharing an id are the same work,
    and a composed task usually differs from the one it replaces — new
    ownership, new acceptance — which is the reason to compose at all.
    """
    rc = _continue(_prior(project, done=("alpha",)), tmp_path, graph)

    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha" in out
    assert "will run again" in out, "and it says so rather than doing it quietly"


def test_skip_done_subtracts_what_the_prior_run_paid_for(project, tmp_path, graph, capsys):
    rc = _continue(_prior(project, done=("alpha",)), tmp_path, graph, skip_done=True)

    out = capsys.readouterr().out
    assert rc == 0
    assert "skipping from prior: alpha" in out
    plan_body = out.split("wave 1", 1)[1]
    assert "beta" in plan_body
    assert "alpha" not in plan_body, "skipping has to reach the plan, not just the prose"


def test_a_plan_with_nothing_left_exits_three(project, tmp_path, graph, capsys):
    """The same code `compose` and `resume` use for the same condition."""
    rc = _continue(
        _prior(project, done=("alpha", "beta")), tmp_path, graph, skip_done=True,
    )

    assert rc == 3
    assert "nothing left to do" in capsys.readouterr().out


def test_nothing_is_dispatched_and_no_state_is_written_without_yes(project, tmp_path, graph):
    _continue(_prior(project), tmp_path, graph)

    runs = tmp_path / "state" / "runs"
    assert sorted(p.name for p in runs.iterdir()) == ["prior"], (
        "a preview must not create a run"
    )


# --- lineage ---------------------------------------------------------------


def test_parent_run_id_survives_save_and_load(tmp_path):
    spec = RunSpec(
        run_id="child", goal="g", project_dir=str(tmp_path),
        graph_name="followup", node_id="rebuild", parent_run_id="prior",
    )
    state = RunState.for_tasks(spec, ["alpha"])
    state.save(tmp_path / "state")

    reloaded = RunState.load(tmp_path / "state", "child")

    assert reloaded is not None
    assert reloaded.spec.parent_run_id == "prior"


def test_a_run_without_a_parent_says_nothing_about_one(project, tmp_path):
    from vise.cli.runtime_cmd import _render_state

    assert "continues" not in _render_state(_prior(project))


def test_status_names_the_run_a_continuation_came_from(tmp_path):
    from vise.cli.runtime_cmd import _render_state

    spec = RunSpec(run_id="child", goal="g", project_dir=str(tmp_path),
                   parent_run_id="prior")
    assert "continues prior" in _render_state(RunState.for_tasks(spec, ["alpha"]))


# --- the ledger actually carrying -----------------------------------------
#
# Everything above stops at the preview. This is the part the whole change
# exists for, and it needs the scheduler.


class _Worker:
    def __init__(self, cost: float = 1.0):
        self.cost = cost
        self.dispatched: list[str] = []

    def run(self, brief):
        self.dispatched.append(brief.task_id)
        target = Path(brief.workdir or brief.project_dir) / "src" / "mod.py"
        target.write_text(f"# {brief.task_id}\n", encoding="utf-8")
        return TaskResult(
            task_id=brief.task_id, verdict=Verdict.PASS, summary="done",
            evidence="$ pytest\n1 passed", checks="$ ruff\nAll checks passed!",
            changed_paths=("src/mod.py",),
            usage=Usage(cost_usd=self.cost, tokens_in=10, tokens_out=20),
        )


def _scheduler(worker, tmp_path):
    from vise.runtime.registry import AgentRegistry
    from vise.runtime.scheduler import Scheduler, SchedulerConfig

    return Scheduler(worker=worker, registry=AgentRegistry.bundled(),
                     state_root=tmp_path, config=SchedulerConfig(verify=False))


def _new_tasks(project):
    from vise.engines.graph_engine import Task

    # "python" in the name is load-bearing: twelve agents take the `backend`
    # role, and the capability hint is what makes the task routable at all.
    return [Task(id="delta", name="Delta python", role="backend",
                 ownership=["src/**"])]


def test_the_continuation_opens_its_ledger_at_the_prior_spend(project, tmp_path):
    """The one thing this change exists for.

    A ceiling that resets between links is not a ceiling: a goal pursued
    across four runs would be under budget in each and unbounded overall.
    """
    prior = _prior(project, spent=3.55)
    worker = _Worker(cost=0.40)
    spec = RunSpec(run_id="child", goal="g", project_dir=str(project),
                   graph_name="followup", node_id="rebuild",
                   budget=RunBudget(max_parallel=2), parent_run_id="prior")

    state = _scheduler(worker, tmp_path).continue_from(prior, spec, _new_tasks(project))

    assert worker.dispatched == ["delta"]
    assert state.ledger.spent.cost_usd == pytest.approx(3.95), (
        "the prior spend plus this run's, not this run's alone"
    )
    assert state.ledger.by_task["alpha"].cost_usd == pytest.approx(3.55), (
        "and it is attributable, not a lump"
    )


def test_the_continuation_starts_the_new_plan_from_scratch(project, tmp_path):
    """Spend carries; records do not.

    Identity is what a new plan may have changed, so the new graph's tasks get
    fresh records rather than inheriting a verdict about different work.
    """
    prior = _prior(project, spent=1.0)
    state = _scheduler(_Worker(), tmp_path).continue_from(
        prior, RunSpec(run_id="child", goal="g", project_dir=str(project),
                       budget=RunBudget(max_parallel=2), parent_run_id="prior"),
        _new_tasks(project),
    )

    assert set(state.tasks) == {"delta"}, "the prior run's records are not carried in"


def test_the_continuation_records_the_link_as_an_event(project, tmp_path):
    prior = _prior(project, spent=2.25)
    state = _scheduler(_Worker(), tmp_path).continue_from(
        prior, RunSpec(run_id="child", goal="g", project_dir=str(project),
                       budget=RunBudget(max_parallel=2), parent_run_id="prior"),
        _new_tasks(project),
    )

    event = next(e for e in state.events if e["kind"] == "continued")
    assert event["parent"] == "prior"
    assert event["inherited_usd"] == pytest.approx(2.25)


def test_the_continued_event_is_a_registered_run_event():
    """The fourth time an unregistered event would have been dropped silently."""
    from vise.engines.telemetry import _VALID_RUN_EVENTS

    assert "continued" in _VALID_RUN_EVENTS
