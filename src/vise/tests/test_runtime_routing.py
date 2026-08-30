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
    FALLBACK,
    LADDER,
    POLICY,
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


def test_a_charter_model_does_not_overrule_the_policy():
    """The charter is a default, not a floor. `docs-writer` declaring sonnet is
    saying what it runs at when nobody else has an opinion — not overruling a
    policy that put documentation on haiku. Getting this backwards made the
    whole haiku tier unreachable through any bundled agent."""
    agent = AgentSpec(id="docs-writer", role="docs", description="d",
                      model="sonnet", effort="low")
    assert route(role="docs", agent=agent).model == "haiku"


def test_a_charter_supplies_the_default_for_a_role_the_policy_does_not_cover():
    agent = AgentSpec(id="designer", role="design", description="d",
                      model="opus", effort="high")
    decision = route(role="design", agent=agent)
    assert (decision.model, decision.effort) == ("opus", "high")
    assert any("not in the policy" in r for r in decision.reasons)


def test_a_role_with_no_policy_and_no_charter_falls_back_to_sonnet_medium():
    decision = route(role="astrology")
    assert (decision.model, decision.effort) == FALLBACK
    assert any("falls back to" in r for r in decision.reasons)


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


# --- the policy table itself ---------------------------------------------


@pytest.mark.parametrize("role,expected", [
    ("extract", ("haiku", "low")),
    ("research", ("haiku", "low")),
    ("classify", ("haiku", "low")),
    ("docs", ("haiku", "medium")),
    ("backend", ("sonnet", "medium")),
    ("frontend", ("sonnet", "medium")),
    ("test", ("sonnet", "medium")),
    ("debug", ("sonnet", "high")),
    ("integration", ("sonnet", "high")),
    ("architecture", ("opus", "high")),
    ("security", ("opus", "high")),
    ("review", ("opus", "high")),
    ("replan", ("opus", "high")),
])
def test_the_policy_table_routes_as_written(role, expected):
    """The whole table, asserted row by row. It is a specification someone wrote
    down; a router that quietly rounds it to the nearest ladder rung is not
    implementing it."""
    decision = route(role=role)
    assert (decision.model, decision.effort) == expected


def test_a_default_that_is_not_a_ladder_rung_survives_routing():
    """haiku/medium is not on the ladder. Reading the result back off the ladder
    rewrote it to haiku/low, which is how the table became unimplementable."""
    assert ("haiku", "medium") not in LADDER
    assert POLICY["docs"] == ("haiku", "medium")
    decision = route(role="docs")
    assert (decision.model, decision.effort) == ("haiku", "medium")


def test_escalating_off_a_non_rung_default_lands_on_the_ladder():
    attempts = [Attempt(1, "haiku", "medium", Verdict.FAIL, "wrong", FailureKind.CODE_BUG)]
    decision = route(role="docs", attempts=attempts)
    assert (decision.model, decision.effort) == LADDER[1]
    assert decision.escalated_from == "haiku/low"


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
