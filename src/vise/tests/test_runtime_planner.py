"""Waves, admission, and the plan a person reads before authorising a run.

The planner is where the other five modules meet, so these tests are the closest
thing this milestone has to an integration suite: if a contract is
underspecified, it shows up here as a plan that cannot be built or cannot be
read.
"""
from __future__ import annotations

import pytest

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


# --- what the plan can say about a width it does not have --------------------


def _wide(cap: int = 0):
    from vise.engines.graph_engine import ForEach

    reg = _registry()
    reg.agents["researcher"] = AgentSpec(
        id="researcher", role="research", description="d", model="sonnet",
        writes=False, capabilities=("research",),
    )
    tasks = [
        Task(id="split", name="Split", role="research", writes=False),
        Task(id="each", name="Each", role="research", writes=False, dependencies=["split"],
             for_each=ForEach(from_task="split", items="sub_questions", max_items=cap)),
        Task(id="synth", name="Synth", role="research", writes=False, dependencies=["each"]),
    ]
    return tasks, reg


def test_an_expanding_task_is_planned_once_with_its_source_and_cap_named():
    tasks, reg = _wide(cap=6)
    result = _plan(tasks, registry=reg)
    planned = {t.task_id: t for w in result.waves for t in w.tasks}
    assert planned["each"].expands is not None
    assert (planned["each"].expands.source, planned["each"].expands.key,
            planned["each"].expands.cap) == ("split", "sub_questions", 6)
    assert planned["split"].expands is None
    rendered = result.render()
    assert "×1..6" in rendered and "split's 'sub_questions'" in rendered
    assert "expands at run time" in rendered and "counts it once" in rendered
    assert result.problems == ()


def test_the_cost_is_a_range_whose_floor_counts_the_expansion_once():
    tasks, reg = _wide(cap=6)
    result = _plan(tasks, registry=reg)
    per_child = {t.task_id: t for w in result.waves for t in w.tasks}["each"].decision.estimated_cost_usd
    assert result.estimated_cost_ceiling_usd == pytest.approx(
        result.estimated_cost_usd + per_child * 5
    )
    assert "up to ~$" in result.render()
    payload = result.to_dict()
    assert payload["estimated_cost_ceiling_usd"] > payload["estimated_cost_usd"]
    assert payload["waves"][1]["tasks"][0]["expands"] == {
        "source": "split", "key": "sub_questions", "cap": 6
    }


def test_an_undeclared_cap_is_planned_at_the_default():
    from vise.runtime.expand import DEFAULT_MAX_ITEMS

    tasks, reg = _wide()
    planned = {t.task_id: t for w in _plan(tasks, registry=reg).waves for t in w.tasks}
    assert planned["each"].expands.cap == DEFAULT_MAX_ITEMS


def test_a_ceiling_that_does_not_fit_is_a_note_not_a_problem():
    tasks, reg = _wide(cap=10)
    floor = _plan(tasks, registry=reg).estimated_cost_usd
    result = _plan(tasks, registry=reg, budget=RunBudget(max_cost_usd=floor + 0.5))
    assert result.problems == ()
    assert any("every expansion reaches its cap" in n and "stop it for a person" in n
               for n in result.notes), result.notes


def test_a_ceiling_that_fits_gets_no_such_note():
    tasks, reg = _wide(cap=2)
    result = _plan(tasks, registry=reg, budget=RunBudget(max_cost_usd=100))
    assert not any("stop it for a person" in n for n in result.notes), result.notes


def test_a_plan_without_an_expansion_reads_exactly_as_before():
    result = _plan([Task(id="t", name="t", role="test", ownership=["tests/**"])])
    assert result.estimated_cost_ceiling_usd == result.estimated_cost_usd
    assert "up to" not in result.render() and "expands" not in result.render()
    assert result.to_dict()["waves"][0]["tasks"][0]["expands"] is None
