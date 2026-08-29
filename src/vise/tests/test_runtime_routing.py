"""Routing is the module most likely to look right and be wrong, because every
one of its mistakes is a plausible number.

Two of these tests exist because the first implementation failed them: `medium`
complexity promoted every task one rung (making the haiku rung unreachable for
any task whose author left the field blank), and an environment failure spent a
rung of the ladder.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from vise.runtime.contracts import Attempt, FailureKind, Verdict
from vise.runtime.registry import AgentSpec
from vise.runtime.routing import (
    LADDER,
    TOP,
    ModelRouter,
    escalation_steps,
    tier_of,
)


@dataclass
class T:
    """A task stub — the router is duck-typed and needs six attributes."""
    id: str = "t"
    name: str = "t"
    role: str = "backend"
    complexity: str = "medium"
    criticality: str = "routine"
    model: str | None = None
    effort: str | None = None


def route(**kw):
    router_kw = {k: kw.pop(k) for k in ("agent", "attempts", "budget_remaining_usd") if k in kw}
    return ModelRouter().route(T(**kw), **router_kw)


def test_a_task_with_no_stated_complexity_is_not_promoted():
    """`medium` is what a blank field becomes, so it has to be a no-op."""
    assert route(role="docs").model == "haiku"
    assert route(role="docs", complexity="medium").tier == route(role="docs").tier


def test_high_complexity_raises_the_floor():
    assert route(complexity="high").effort == "high"


def test_low_complexity_never_lowers_a_role_rung():
    assert route(role="review", complexity="trivial").model == "opus"


def test_critical_criticality_pins_the_top_rung():
    decision = route(role="docs", criticality="critical")
    assert (decision.model, decision.effort) == LADDER[TOP]
    assert any("critical" in r for r in decision.reasons)


def test_elevated_criticality_adds_exactly_one_rung():
    assert route(criticality="elevated").tier == route().tier + 1


def test_a_failed_attempt_climbs_one_rung_and_says_so():
    attempts = [Attempt(1, "sonnet", "medium", Verdict.FAIL, "wrong", FailureKind.CODE_BUG)]
    decision = route(attempts=attempts)
    assert decision.tier == route().tier + 1
    assert decision.escalated_from == "sonnet/medium"


def test_an_environment_failure_is_a_retry_not_an_escalation():
    """Nothing about a missing binary is evidence a bigger model would do better."""
    attempts = [Attempt(1, "sonnet", "medium", Verdict.FAIL, "no psql",
                        FailureKind.ENVIRONMENT_BUG)]
    assert route(attempts=attempts).tier == route().tier


def test_an_inconclusive_attempt_does_not_escalate():
    attempts = [Attempt(1, "sonnet", "medium", Verdict.INCONCLUSIVE, "db down")]
    assert escalation_steps(attempts) == 0


def test_a_passing_attempt_does_not_escalate():
    assert escalation_steps([Attempt(1, "haiku", "low", Verdict.PASS)]) == 0


def test_the_ladder_terminates_and_names_the_next_move():
    attempts = [
        Attempt(i, "sonnet", "medium", Verdict.FAIL, "wrong", FailureKind.CODE_BUG)
        for i in range(1, 8)
    ]
    decision = route(attempts=attempts)
    assert decision.tier == TOP
    assert any("replan" in r for r in decision.reasons)


def test_a_charter_pin_raises_the_floor_but_does_not_cap_escalation():
    agent = AgentSpec(id="reviewer", role="review", description="d", model="opus")
    base = route(role="docs", agent=agent)
    assert base.model == "opus"
    assert any("charter pins" in r for r in base.reasons)


def test_a_charter_pin_below_the_computed_rung_does_not_lower_it():
    agent = AgentSpec(id="cheap", role="review", description="d", model="haiku")
    assert route(role="review", agent=agent).model == "opus"


def test_a_task_pin_is_absolute_and_survives_escalation():
    attempts = [
        Attempt(i, "sonnet", "medium", Verdict.FAIL, "wrong", FailureKind.CODE_BUG)
        for i in range(1, 5)
    ]
    decision = route(model="haiku", effort="low", attempts=attempts)
    assert (decision.model, decision.effort) == ("haiku", "low")
    assert decision.pinned is True
    assert decision.escalated_from is None


def test_an_effort_pin_alone_keeps_the_routed_model():
    decision = route(effort="max")
    assert decision.model == "sonnet" and decision.effort == "max"


def test_budget_vetoes_rather_than_silently_downgrading():
    decision = route(role="review", budget_remaining_usd=0.10)
    assert decision.model == "opus", "a vetoed decision keeps its model; the run stops"
    assert decision.affordable is False
    assert "stops rather than silently dropping" in " ".join(decision.reasons)


def test_budget_never_promotes():
    rich = route(budget_remaining_usd=1000.0)
    assert rich.tier == route().tier


def test_every_decision_carries_its_reasons():
    assert route().reasons, "a decision nobody can read back is one nobody can correct"


def test_tier_of_matches_model_and_effort_then_model_alone():
    assert tier_of("sonnet", "high") == 2
    assert tier_of("sonnet") == 1
    assert tier_of("gpt-9") is None


def test_render_shows_the_escalation_and_the_cost():
    attempts = [Attempt(1, "sonnet", "medium", Verdict.FAIL, "wrong", FailureKind.CODE_BUG)]
    text = route(attempts=attempts).render("backend-auth")
    assert "backend-auth" in text
    assert "escalated from" in text
    assert "estimated cost" in text


def test_decision_serialises():
    assert route().to_dict()["model"] == "sonnet"


def test_an_unknown_role_starts_at_the_implementation_rung():
    """Too low fails visibly and escalates; too high just costs money."""
    assert route(role="astrology").tier == pytest.approx(1)


def test_a_haiku_pin_is_priced_as_haiku():
    """Rung 0 is falsy. `tier_of(...) or fallback` priced a haiku pin as sonnet."""
    decision = route(model="haiku", effort="low")
    assert decision.tier == 0
    assert decision.estimated_cost_usd == pytest.approx(0.05)


def test_a_pin_with_an_off_ladder_effort_keeps_the_pinned_model_rung():
    decision = route(model="haiku", effort="max")
    assert (decision.model, decision.effort) == ("haiku", "max")
    assert decision.tier == 0


def test_an_unclassified_failure_still_escalates():
    """Undiagnosed is not 'fine'. Reading it as an environment problem would
    let a task loop at the cheapest rung forever."""
    attempts = [Attempt(1, "sonnet", "medium", Verdict.FAIL, "no idea")]
    assert escalation_steps(attempts) == 1
