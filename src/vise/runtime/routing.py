"""Which model, which effort, and why — see docs/model-routing.md.

Picking a model per task rather than per session is most of the cost difference
between an orchestrator worth running and one that is not. Picking it badly is
worse than not picking at all: a task quietly downgraded to a model that cannot
do it fails in a way indistinguishable from a hard problem.

So every decision this module makes carries the reasons that produced it. A
router whose choices cannot be read back is a router nobody can correct, and the
first time it spends four dollars on a two-line change the only available
response is to switch it off.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from vise.runtime.contracts import (
    RETRY_KINDS,
    Attempt,
    Complexity,
    Criticality,
    Verdict,
)

#: The escalation ladder, cheapest first. One rung per failed attempt where the
#: work was attempted and was wrong. Nothing climbs past the last rung — a task
#: that fails there goes to replanning, which asks a different question.
LADDER: tuple[tuple[str, str], ...] = (
    ("haiku", "low"),
    ("sonnet", "medium"),
    ("sonnet", "high"),
    ("opus", "high"),
)

TOP = len(LADDER) - 1

#: Coarse per-task cost estimates, in USD, for planning only — never billing.
#: Anchored on a measured sweep of mini-vise's three-node pipeline ($0.82 per
#: run on sonnet, $2.09 on opus, effort pinned at medium; see
#: docs/model-routing.md). They exist so a plan can say "this will cost roughly
#: four dollars" before anything runs, which is the number that decides whether
#: to run it.
TIER_COST_USD: tuple[float, ...] = (0.05, 0.85, 1.20, 2.10)

#: Starting rung by role. Everything not listed starts at implementation level,
#: which is the safe default: too low fails visibly and escalates, too high just
#: costs money.
_BASE_BY_ROLE: dict[str, int] = {
    "research": 0,
    "inventory": 0,
    "docs": 0,
    "backend": 1,
    "frontend": 1,
    "test": 1,
    "migration": 2,
    "debug": 2,
    "integration": 2,
    "design": 3,
    "security": 3,
    "review": 3,
    # Verification is per task and therefore a volume operation: it checks a
    # diff against an explicit checklist, which is closer to applying than to
    # noticing. The open-ended adversarial pass that genuinely needs the big
    # model is `review`, and it runs once per node rather than once per task.
    "verify": 1,
    "replan": 3,
}

#: Complexity raises the floor and never lowers it. Only ``high`` raises
#: anything: ``medium`` is the default a task gets when nobody stated a
#: complexity, so it has to be a no-op. A neutral default that promotes is a
#: router that spends the budget because it is there — and it would make the
#: haiku rung unreachable for every task whose author simply did not fill the
#: field in.
#:
#: Lowering is not available at any value. A ``trivial`` task on a role whose
#: base rung is high stays high: the rung was chosen for what the work *is*, and
#: one instance looking easy is not evidence against it.
_FLOOR_BY_COMPLEXITY: dict[str, int] = {
    Complexity.TRIVIAL.value: 0,
    Complexity.LOW.value: 0,
    Complexity.MEDIUM.value: 0,
    Complexity.HIGH.value: 2,
}


def tier_of(model: str, effort: str | None = None) -> int | None:
    """The ladder rung for a model (and optionally an effort), or None."""
    for i, (m, e) in enumerate(LADDER):
        if m == model and (effort is None or e == effort):
            return i
    for i, (m, _) in enumerate(LADDER):
        if m == model:
            return i
    return None


@dataclass(frozen=True)
class RoutingDecision:
    """A model choice and the complete argument for it."""

    model: str
    effort: str
    tier: int
    reasons: tuple[str, ...] = ()
    escalated_from: str | None = None
    pinned: bool = False
    estimated_cost_usd: float = 0.0
    affordable: bool = True

    def render(self, task_id: str = "") -> str:
        head = f"{task_id + '  ' if task_id else ''}{self.model}/{self.effort}"
        lines = [head]
        if self.escalated_from:
            lines.append(f"  escalated from {self.escalated_from}")
        lines += [f"    · {r}" for r in self.reasons]
        lines.append(f"  estimated cost: ~${self.estimated_cost_usd:.2f}")
        if not self.affordable:
            lines.append("  NOT AFFORDABLE under the remaining run budget")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "effort": self.effort,
            "tier": self.tier,
            "reasons": list(self.reasons),
            "escalated_from": self.escalated_from,
            "pinned": self.pinned,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "affordable": self.affordable,
        }


def escalation_steps(attempts: Iterable[Attempt]) -> int:
    """How many rungs the history has earned.

    Only failures where the work was attempted and was wrong count. A timeout or
    a missing binary is a retry at the same rung: nothing about it is evidence
    that a larger model would have done better, and escalating on it spends the
    top tier on an unconfigured tool.

    A failure with no classification counts. An undiagnosed failure is not
    evidence that the work was fine, and reading "nobody said" as "environment
    problem" would let a task loop at the cheapest rung forever.
    """
    steps = 0
    for a in attempts:
        if a.verdict is Verdict.PASS:
            continue
        if a.verdict is Verdict.INCONCLUSIVE:
            continue
        if a.classification in RETRY_KINDS:
            continue
        steps += 1
    return steps


@dataclass
class ModelRouter:
    """Turns a task plus its history into a model, an effort, and an argument."""

    base_by_role: dict[str, int] = field(default_factory=lambda: dict(_BASE_BY_ROLE))
    ladder: tuple[tuple[str, str], ...] = LADDER

    def route(
        self,
        task: Any,
        *,
        agent: Any = None,
        attempts: Iterable[Attempt] = (),
        budget_remaining_usd: float | None = None,
    ) -> RoutingDecision:
        """Decide a model and effort for one task.

        ``task`` is duck-typed on purpose — it is ``graph_engine.Task`` in
        production and a stub in tests, and the router needs six attributes, not
        an import of the control plane.
        """
        attempts = tuple(attempts)
        reasons: list[str] = []

        role = getattr(task, "role", None) or ""
        base = self.base_by_role.get(role, 1)
        reasons.append(f"role {role or '(none)'} starts at {self._name(base)}")
        tier = base

        complexity = str(getattr(task, "complexity", Complexity.MEDIUM.value))
        floor = _FLOOR_BY_COMPLEXITY.get(complexity, 1)
        if floor > tier:
            reasons.append(f"complexity {complexity} raises the floor to {self._name(floor)}")
            tier = floor

        # A charter that names a model did so for a reason the router does not
        # have. It sets the floor, not the ceiling: escalation may still climb
        # above it when attempts fail, but nothing drops below it.
        charter_model = getattr(agent, "model", None) if agent is not None else None
        if charter_model:
            charter_tier = tier_of(str(charter_model))
            if charter_tier is not None and charter_tier > tier:
                agent_id = getattr(agent, "id", "charter")
                reasons.append(
                    f"{agent_id} charter pins {charter_model}, raising the floor to "
                    f"{self._name(charter_tier)}"
                )
                tier = charter_tier

        criticality = str(getattr(task, "criticality", Criticality.ROUTINE.value))
        if criticality == Criticality.CRITICAL.value:
            if tier < TOP:
                reasons.append("criticality critical pins the top rung")
            tier = TOP
        elif criticality == Criticality.ELEVATED.value and tier < TOP:
            reasons.append("criticality elevated adds a rung")
            tier += 1

        before = tier
        steps = escalation_steps(attempts)
        if steps:
            tier = min(TOP, tier + steps)
            failed = len([a for a in attempts if a.verdict is Verdict.FAIL])
            reasons.append(
                f"{failed} prior attempt(s), {steps} of them the work being wrong"
            )
            if tier == TOP and before + steps > TOP:
                reasons.append("ladder exhausted — a further failure is a replan, not a rung")

        escalated_from = self._name(before) if tier != before else None

        # Task pins are absolute. A person who wrote `model:` into the workflow
        # made a decision the router has no standing to overrule — including by
        # escalating past it.
        pinned = False
        pin_model = getattr(task, "model", None)
        pin_effort = getattr(task, "effort", None)
        if pin_model:
            pinned = True
            model = str(pin_model)
            # `or` chaining is wrong here: rung 0 is falsy, so a haiku pin fell
            # through to the computed rung and was priced as sonnet. Rungs are
            # indices, and an index needs an explicit None check.
            pin_tier = tier_of(model)
            effort = str(pin_effort or self.ladder[pin_tier if pin_tier is not None else tier][1])
            exact = tier_of(model, effort)
            if exact is not None:
                tier = exact
            elif pin_tier is not None:
                tier = pin_tier
            reasons = [f"task pins {model}/{effort} — the router does not overrule a task pin"]
            escalated_from = None
        else:
            model, effort = self.ladder[tier]
            if pin_effort:
                effort = str(pin_effort)
                reasons.append(f"task pins effort {effort}")

        cost = TIER_COST_USD[min(tier, len(TIER_COST_USD) - 1)]
        affordable = budget_remaining_usd is None or cost <= budget_remaining_usd
        if not affordable:
            reasons.append(
                f"remaining budget ${budget_remaining_usd:.2f} < estimated ${cost:.2f} — "
                f"the run stops rather than silently dropping to a cheaper model"
            )

        return RoutingDecision(
            model=model,
            effort=effort,
            tier=tier,
            reasons=tuple(reasons),
            escalated_from=escalated_from,
            pinned=pinned,
            estimated_cost_usd=cost,
            affordable=affordable,
        )

    def _name(self, tier: int) -> str:
        m, e = self.ladder[max(0, min(tier, TOP))]
        return f"{m}/{e}"
