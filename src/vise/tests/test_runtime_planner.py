"""Waves, admission, and the plan a person reads before authorising a run.

The planner is where the other five modules meet, so these tests are the closest
thing this milestone has to an integration suite: if a contract is
underspecified, it shows up here as a plan that cannot be built or cannot be
read.
"""
from __future__ import annotations

from vise.engines.graph_engine import Task
from vise.runtime.contracts import RunBudget
from vise.runtime.planner import RunPlan, dependency_waves, plan
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.routing import ModelRouter


def _registry() -> AgentRegistry:
    """A small hand-built registry — the bundled one is exercised elsewhere, and
    pinning plan shapes to whichever charters happen to ship makes these tests
    fail for reasons that have nothing to do with the planner."""
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d",
                  model="sonnet", capabilities=("backend", "python")),
        AgentSpec(id="frontend", role="frontend", description="d", model="sonnet",
                  capabilities=("frontend",)),
        AgentSpec(id="tester", role="test", description="d", model="sonnet",
                  capabilities=("test",)),
        AgentSpec(id="reviewer", role="review", description="d", model="opus",
                  writes=False, capabilities=("review",)),
    ):
        reg.agents[spec.id] = spec
    return reg


def _plan(tasks, **kw) -> RunPlan:
    kw.setdefault("registry", _registry())
    kw.setdefault("router", ModelRouter())
    return plan(tasks, **kw)


# --- wave derivation ------------------------------------------------------


def test_independent_tasks_share_the_first_wave():
    tasks = [Task(id="a", name="a"), Task(id="b", name="b")]
    waves, leftover = dependency_waves(tasks)
    assert [[t.id for t in w] for w in waves] == [["a", "b"]]
    assert leftover == []


def test_a_dependency_pushes_a_task_into_the_next_wave():
    tasks = [Task(id="a", name="a"), Task(id="b", name="b", dependencies=["a"])]
    waves, _ = dependency_waves(tasks)
    assert [[t.id for t in w] for w in waves] == [["a"], ["b"]]


def test_completed_tasks_are_not_replanned():
    tasks = [Task(id="a", name="a"), Task(id="b", name="b", dependencies=["a"])]
    waves, _ = dependency_waves(tasks, completed=["a"])
    assert [[t.id for t in w] for w in waves] == [["b"]]


def test_a_cycle_is_reported_rather_than_hanging():
    tasks = [
        Task(id="a", name="a", dependencies=["b"]),
        Task(id="b", name="b", dependencies=["a"]),
    ]
    waves, leftover = dependency_waves(tasks)
    assert waves == [] and sorted(leftover) == ["a", "b"]


def test_an_unknown_dependency_leaves_the_task_unschedulable():
    tasks = [Task(id="a", name="a", dependencies=["ghost"])]
    _, leftover = dependency_waves(tasks)
    assert leftover == ["a"]


def test_runnable_tasks_still_plan_around_a_broken_one():
    """Failing on the first problem hides every other one."""
    tasks = [
        Task(id="ok", name="ok", role="test", ownership=["tests/**"]),
        Task(id="bad", name="bad", role="test", dependencies=["ghost"]),
    ]
    result = _plan(tasks)
    assert result.task_count == 1
    assert any("unschedulable" in p for p in result.problems)


# --- ownership shapes the waves ------------------------------------------


def test_conflicting_owners_cannot_share_a_wave():
    tasks = [
        Task(id="auth", name="auth", role="backend", ownership=["src/auth/**"]),
        Task(id="wide", name="wide", role="backend", ownership=["src/**"]),
    ]
    result = _plan(tasks)
    ids_per_wave = sorted([t.task_id for t in w.tasks] for w in result.waves)
    assert ids_per_wave == [["auth"], ["wide"]]


def test_disjoint_owners_share_a_wave():
    tasks = [
        Task(id="auth", name="auth", role="backend", ownership=["src/auth/**"]),
        Task(id="web", name="web", role="frontend", ownership=["web/**"]),
    ]
    result = _plan(tasks)
    assert len(result.waves) == 1


def test_a_read_only_task_never_holds_a_claim():
    tasks = [
        Task(id="wide", name="wide", role="backend", ownership=["src/**"]),
        Task(id="review", name="review", role="review", writes=False),
    ]
    result = _plan(tasks)
    assert len(result.waves) == 1, "a reviewer touches nothing and blocks nothing"


def test_max_parallel_chunks_a_wave():
    tasks = [
        Task(id=f"t{i}", name=f"t{i}", role="frontend", ownership=[f"web/{i}/**"])
        for i in range(5)
    ]
    result = _plan(tasks, budget=RunBudget(max_parallel=2))
    assert [len(w.tasks) for w in result.waves] == [2, 2, 1]


# --- routing inside the plan ---------------------------------------------


def test_a_task_with_no_role_is_a_problem_not_a_guess():
    result = _plan([Task(id="mystery", name="mystery")])
    assert any("declares no role" in p for p in result.problems)
    assert result.waves[0].tasks[0].agent_id is None


def test_an_unroutable_role_names_what_would_fix_it():
    result = _plan([Task(id="t", name="t", role="astrology")])
    assert any("unroutable" in p and "astrology" in p for p in result.problems)


def test_a_capability_in_the_task_id_reaches_the_right_agent():
    reg = _registry()
    reg.agents["backend-rust"] = AgentSpec(
        id="backend-rust", role="backend", description="d", model="sonnet",
        capabilities=("backend", "rust"),
    )
    result = plan([Task(id="backend-rust-parser", name="parser", role="backend",
                        ownership=["src/**"])], registry=reg)
    assert result.waves[0].tasks[0].agent_id == "backend-rust"


def test_the_plan_costs_itself():
    tasks = [Task(id="t", name="t", role="review", writes=False)]
    result = _plan(tasks)
    assert result.estimated_cost_usd > 0


def test_a_task_that_does_not_fit_the_budget_is_a_problem():
    tasks = [Task(id="t", name="t", role="review", writes=False)]
    result = _plan(tasks, budget=RunBudget(max_cost_usd=0.10))
    assert any("budget" in p for p in result.problems)


def test_budget_is_consumed_across_the_plan_not_reset_per_task():
    """Two opus tasks against a one-opus budget: the second must not fit."""
    tasks = [
        Task(id="r1", name="r1", role="review", writes=False),
        Task(id="r2", name="r2", role="review", writes=False),
    ]
    result = _plan(tasks, budget=RunBudget(max_cost_usd=2.50))
    assert len([p for p in result.problems if "budget" in p]) == 1


# --- the rendered plan ----------------------------------------------------


def test_the_rendered_plan_names_the_agent_the_model_and_the_reasons():
    tasks = [Task(id="auth", name="auth", role="backend", ownership=["src/auth/**"],
                  criticality="elevated")]
    text = _plan(tasks).render()
    assert "backend-python" in text
    assert "sonnet/high" in text
    assert "criticality elevated adds a rung" in text
    assert "total: 1 task(s)" in text


def test_the_rendered_plan_marks_an_unroutable_task():
    assert "UNROUTABLE" in _plan([Task(id="t", name="t")]).render()


def test_the_plan_serialises():
    tasks = [Task(id="t", name="t", role="test", ownership=["tests/**"])]
    data = _plan(tasks).to_dict()
    assert data["task_count"] == 1
    assert data["waves"][0]["tasks"][0]["routing"]["model"] == "sonnet"


def test_an_empty_node_plans_to_an_empty_plan():
    result = _plan([])
    assert result.waves == () and result.task_count == 0
