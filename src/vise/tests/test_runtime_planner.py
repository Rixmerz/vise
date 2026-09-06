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
    assert any("widest and longest" in n and "stop it for a person" in n
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


# --- verification is work the run will do, so the plan counts it ------------


def _verified(**kw):
    reg = _registry()
    reg.agents["verifier"] = AgentSpec(id="verifier", role="verify", description="d",
                                       model="sonnet", writes=False, capabilities=("verify",))
    base = dict(id="t", name="t", role="backend", acceptance=["it works"])
    base.update(kw)
    return [Task(**base)], reg


def test_a_verified_task_is_priced_with_its_verifier():
    """The plan used to omit verification entirely, understating every verified
    run by a model call — invisibly, and by three once a panel is declared."""
    from vise.runtime.routing import TIER_COST_USD, tier_of

    tasks, reg = _verified()
    one = _plan(tasks, registry=reg)
    planned = one.waves[0].tasks[0]
    assert planned.verifiers == 1
    assert one.estimated_cost_usd == pytest.approx(
        planned.decision.estimated_cost_usd + TIER_COST_USD[tier_of("sonnet", "medium")]
    )


def test_a_panel_is_priced_as_its_own_number_of_verifier_runs():
    # The expected number comes from the routing table, not from the plan: the
    # first draft derived it from `estimated_cost_usd` and so agreed with a
    # planner that priced no verifier at all.
    from vise.runtime.routing import TIER_COST_USD, tier_of

    one_verifier = TIER_COST_USD[tier_of("sonnet", "medium")]
    tasks, reg = _verified(verifiers=3)
    panel = _plan(tasks, registry=reg)
    planned = panel.waves[0].tasks[0]

    assert planned.verifiers == 3
    assert planned.cost_usd == pytest.approx(
        planned.decision.estimated_cost_usd + 3 * one_verifier
    )
    assert panel.estimated_cost_usd == pytest.approx(planned.cost_usd)
    assert "+3 verifiers" in panel.render()
    assert panel.to_dict()["waves"][0]["tasks"][0]["verifiers"] == 3


def test_a_task_with_no_criteria_is_priced_without_a_verifier():
    """The scheduler does not verify one, so the plan must not charge for it."""
    tasks, reg = _verified(acceptance=[], verifiers=3)
    result = _plan(tasks, registry=reg)
    assert result.waves[0].tasks[0].verifiers == 0
    assert result.estimated_cost_usd == pytest.approx(
        result.waves[0].tasks[0].decision.estimated_cost_usd
    )


def test_verification_switched_off_is_not_priced():
    tasks, reg = _verified(verifiers=3)
    off = _plan(tasks, registry=reg, verify=False)
    assert off.waves[0].tasks[0].verifiers == 0
    assert "verifiers" not in off.render()


def test_a_registry_that_staffs_no_verifier_prices_none():
    """The scheduler will not dispatch a verifier it cannot resolve."""
    tasks, _ = _verified(verifiers=3)
    assert _plan(tasks, registry=_registry()).waves[0].tasks[0].verifiers == 0


def test_a_panel_multiplies_across_an_expansion():
    from vise.engines.graph_engine import ForEach

    tasks, reg = _wide(cap=4)
    reg.agents["verifier"] = AgentSpec(id="verifier", role="verify", description="d",
                                       model="sonnet", writes=False, capabilities=("verify",))
    tasks[1] = Task(id="each", name="Each", role="research", writes=False,
                    dependencies=["split"], acceptance=["answered"], verifiers=2,
                    for_each=ForEach(from_task="split", items="sub_questions", max_items=4))
    result = _plan(tasks, registry=reg)
    each = {t.task_id: t for w in result.waves for t in w.tasks}["each"]
    # The join runs no worker and is never verified — but its children inherit
    # its criteria and its panel, and the children are the work. So the
    # per-instance cost is a child's, verification included.
    assert each.verifiers == 2
    assert each.cost_usd > each.decision.estimated_cost_usd
    assert each.ceiling_usd == pytest.approx(each.cost_usd * 4)


# --- a sweep costs every round it may take ---------------------------------


def _sweeping(**kw):
    from vise.engines.graph_engine import Until

    reg = _registry()
    reg.agents["researcher"] = AgentSpec(id="researcher", role="research", description="d",
                                         model="sonnet", writes=False,
                                         capabilities=("research",))
    base = dict(id="hunt", name="Hunt", role="research", writes=False,
                until=Until(key="findings", stable_for=2, max_rounds=5))
    base.update(kw)
    return [Task(**base)], reg


def test_a_sweeps_floor_is_its_quiet_round_requirement_not_one():
    """A task needs `stable_for` quiet rounds to stop, so the earliest it can
    finish is that many rounds — pricing it at one understates every sweep."""
    tasks, reg = _sweeping()
    result = _plan(tasks, registry=reg)
    planned = result.waves[0].tasks[0]
    assert (planned.repeats.least, planned.repeats.most) == (2, 5)
    assert planned.cost_usd == pytest.approx(planned.round_cost_usd * 2)
    assert planned.ceiling_usd == pytest.approx(planned.round_cost_usd * 5)
    assert "×2..5 rounds" in result.render()
    assert any("repeats until it stops finding" in n for n in result.notes)


def test_a_sweep_that_can_only_run_once_is_priced_once():
    from vise.engines.graph_engine import Until

    tasks, reg = _sweeping(until=Until(key="f", stable_for=1, max_rounds=1))
    planned = _plan(tasks, registry=reg).waves[0].tasks[0]
    assert (planned.repeats.least, planned.repeats.most) == (1, 1)
    assert planned.cost_usd == pytest.approx(planned.ceiling_usd)


def test_rounds_and_width_compose_because_a_child_inherits_the_sweep():
    """Four items each sweeping four times is sixteen rounds, and the plan has
    to say so before anyone runs it."""
    from vise.engines.graph_engine import ForEach, Until

    tasks, reg = _sweeping(
        dependencies=["split"],
        for_each=ForEach(from_task="split", items="areas", max_items=4),
        until=Until(key="findings", stable_for=1, max_rounds=4),
    )
    tasks.insert(0, Task(id="split", name="Split", role="research", writes=False))
    result = _plan(tasks, registry=reg)
    hunt = {t.task_id: t for w in result.waves for t in w.tasks}["hunt"]
    assert hunt.ceiling_usd == pytest.approx(hunt.round_cost_usd * 4 * 4)
    assert "widest and longest" in result.render()


def test_a_task_without_until_carries_no_repeats():
    result = _plan([Task(id="t", name="t", role="test", ownership=["tests/**"])])
    planned = result.waves[0].tasks[0]
    assert planned.repeats is None
    assert planned.cost_usd == pytest.approx(planned.ceiling_usd)
    assert "rounds" not in result.render()
    assert result.to_dict()["waves"][0]["tasks"][0]["repeats"] is None
