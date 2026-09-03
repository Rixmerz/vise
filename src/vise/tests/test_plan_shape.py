"""A plan must not let a number imply a capability the graph does not have.

Every one of the nine runs vise has performed declared `max_parallel: 3`.
Measured from their event timelines, peak concurrency was 2 in the five that
got that far and 1 in the rest. That is not a scheduler bug — the workload's
dependencies are a chain, and only its last two tasks can overlap. The defect
was that the plan never said so: it reported waves, cost and problems, and let
a reader conclude from `max_parallel: 3` that the run would be wide.

`concurrency_ceiling` is deliberately computed from the RAW dependency waves
with the cap lifted. Reading it off the rendered waves would be circular —
those are already narrowed by `max_parallel`, so a graph capped at 3 would
always report 3, and the number would confirm the budget instead of testing it.
"""
from __future__ import annotations

from vise.engines.graph_engine import Task
from vise.runtime.contracts import RunBudget
from vise.runtime.planner import plan
from vise.runtime.registry import AgentRegistry, AgentSpec


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.agents["backend-python"] = AgentSpec(
        id="backend-python", role="backend", description="d",
        model="sonnet", effort="medium",
    )
    return reg


def _plan(tasks, max_parallel: int = 4):
    return plan(tasks, registry=_registry(), budget=RunBudget(max_parallel=max_parallel))


def _task(task_id: str, deps=(), ownership=("src/*",), writes=True) -> Task:
    return Task(
        id=task_id, name=task_id, role="backend",
        dependencies=list(deps), ownership=list(ownership), writes=writes,
    )


# --- the ceiling -----------------------------------------------------------


def test_a_chain_can_never_run_more_than_one_at_a_time():
    tasks = [
        _task("a", ownership=("src/a/**",)),
        _task("b", deps=["a"], ownership=("src/b/**",)),
        _task("c", deps=["b"], ownership=("src/c/**",)),
    ]

    result = _plan(tasks)

    assert result.concurrency_ceiling == 1
    assert result.critical_path == 3


def test_independent_tasks_reach_their_own_count():
    tasks = [_task(x, ownership=(f"src/{x}/**",)) for x in ("a", "b", "c", "d")]

    result = _plan(tasks)

    assert result.concurrency_ceiling == 4
    assert result.critical_path == 1


def test_tasks_that_claim_the_same_path_count_as_one():
    """Ownership is a real constraint, not a preference — they are never
    dispatched together, so the ceiling must not pretend otherwise."""
    tasks = [_task(x, ownership=("src/shared.py",)) for x in ("a", "b", "c")]

    result = _plan(tasks)

    assert result.concurrency_ceiling == 1
    assert result.critical_path == 1, "they do not depend on each other"


def test_read_only_tasks_pack_freely_despite_sharing_paths():
    tasks = [_task(x, ownership=("src/shared.py",), writes=False) for x in ("a", "b", "c")]

    assert _plan(tasks).concurrency_ceiling == 3


def test_the_ceiling_is_not_the_budget_reported_back():
    """The circularity this number exists to avoid: four independent tasks
    capped at 2 still have a ceiling of 4, because the cap is a choice and the
    ceiling is a property of the graph."""
    tasks = [_task(x, ownership=(f"src/{x}/**",)) for x in ("a", "b", "c", "d")]

    result = _plan(tasks, max_parallel=2)

    assert result.concurrency_ceiling == 4
    assert max(len(w.tasks) for w in result.waves) == 2, "the rendered waves DO obey the cap"


def test_a_single_task():
    result = _plan([_task("a")])
    assert (result.concurrency_ceiling, result.critical_path) == (1, 1)


def test_the_shape_that_started_this():
    """The ledger workload, verbatim: a chain of four, then two that fan out.

    Its runs peaked at 2 against a declared 3. The planner must now predict
    that number before anyone spends money on it.
    """
    tasks = [
        _task("money", ownership=("src/ledger/money.py",)),
        _task("parser", deps=["money"], ownership=("src/ledger/parser.py",)),
        _task("report", deps=["parser"], ownership=("src/ledger/report.py",)),
        _task("cli", deps=["report"], ownership=("src/ledger/cli.py",)),
        _task("tests", deps=["cli"], ownership=("tests/**",)),
        _task("docs", deps=["cli"], ownership=("README.md",)),
    ]

    result = _plan(tasks, max_parallel=3)

    assert result.concurrency_ceiling == 2, "the measured peak of every real run"
    assert result.critical_path == 5


# --- the note --------------------------------------------------------------


def test_over_declared_parallelism_is_a_note_not_a_problem():
    tasks = [
        _task("a", ownership=("src/a/**",)),
        _task("b", deps=["a"], ownership=("src/b/**",)),
    ]

    result = _plan(tasks, max_parallel=4)

    assert result.problems == (), "an honest observation must not refuse to run"
    assert len(result.notes) == 1
    note = result.notes[0]
    assert "4" in note and "1" in note, note


def test_a_budget_the_graph_can_use_gets_no_note():
    tasks = [_task(x, ownership=(f"src/{x}/**",)) for x in ("a", "b")]
    assert _plan(tasks, max_parallel=2).notes == ()


def test_a_budget_smaller_than_the_ceiling_gets_no_note():
    """Under-declaring is a deliberate choice — it costs less and takes longer."""
    tasks = [_task(x, ownership=(f"src/{x}/**",)) for x in ("a", "b", "c", "d")]
    assert _plan(tasks, max_parallel=2).notes == ()


def test_the_shape_reaches_the_rendered_plan_and_the_dict():
    tasks = [
        _task("a", ownership=("src/a/**",)),
        _task("b", deps=["a"], ownership=("src/b/**",)),
    ]

    result = _plan(tasks, max_parallel=3)

    rendered = result.render()
    assert "at most 1 task(s) can run at once" in rendered
    assert "longest chain is 2" in rendered
    assert "note:" in rendered

    payload = result.to_dict()
    assert payload["concurrency_ceiling"] == 1
    assert payload["critical_path"] == 2
    assert len(payload["notes"]) == 1
