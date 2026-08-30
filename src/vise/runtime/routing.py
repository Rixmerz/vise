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

#: The default model and effort per kind of work. This table is the policy; the
#: ladder above is only the path escalation walks when this default fails.
#:
#: Keeping them separate matters. An earlier version mapped each role to a ladder
#: *index*, which made any default that is not a rung — documentation at
#: haiku/medium — inexpressible, and quietly rewrote it to the nearest rung.
#: A policy that cannot state its own defaults is not a policy.
POLICY: dict[str, tuple[str, str]] = {
    # extraction, simple research, classification
    "extract": ("haiku", "low"),
    "research": ("haiku", "low"),
    "inventory": ("haiku", "low"),
    "classify": ("haiku", "low"),
    # documentation
    "docs": ("haiku", "medium"),
    # ordinary coding
    "backend": ("sonnet", "medium"),
    "frontend": ("sonnet", "medium"),
    # testing
    "test": ("sonnet", "medium"),
    # debugging
    "debug": ("sonnet", "high"),
    # integration
    "integration": ("sonnet", "high"),
    # architecture
    "architecture": ("opus", "high"),
    # security-critical work
    "security": ("opus", "high"),
    # adversarial review
    "review": ("opus", "high"),
    # replanning
    "replan": ("opus", "high"),
}

#: What a role not in the table falls back to, before the agent's own default.
FALLBACK: tuple[str, str] = ("sonnet", "medium")

#: Complexity raises the floor and never lowers it. Only ``high`` raises
#: anything: ``medium`` is the default a task gets when nobody stated a
#: complexity, so it has to be a no-op. A neutral default that promotes is a
#: router that spends the budget because it is there — and it would put every
#: task whose author simply did not fill the field in one rung above its policy.
#:
#: Lowering is not available at any value. A ``trivial`` task on a role whose
#: policy is high stays high: the policy was chosen for what the work *is*, and
#: one instance looking easy is not evidence against it.
_FLOOR_BY_COMPLEXITY: dict[str, int] = {
    Complexity.TRIVIAL.value: 0,
    Complexity.LOW.value: 0,
    Complexity.MEDIUM.value: 0,
    Complexity.HIGH.value: 2,
}


def _position(model: str, effort: str) -> int:
    """Where a (model, effort) default sits on the escalation ladder.

    A default need not be a rung — haiku/medium is not — so this falls back to
    the first rung carrying that model. The rung is only used to decide what
    escalation climbs *to*; it never rewrites the default itself.
    """
    exact = tier_of(model, effort)
    if exact is not None:
        return exact
    by_model = tier_of(model)
    return by_model if by_model is not None else 1


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

    policy: dict[str, tuple[str, str]] = field(default_factory=lambda: dict(POLICY))
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

        Precedence, highest first: a model the *task* pins, then the policy for
        its kind of work, then the agent charter's own default. The charter is a
        default and not a floor — an agent that declares ``sonnet`` is saying
        what it runs at when nobody else has an opinion, not overruling a policy
        that put documentation on haiku.

        ``task`` is duck-typed on purpose — it is ``graph_engine.Task`` in
        production and a stub in tests, and the router needs six attributes, not
        an import of the control plane.
        """
        attempts = tuple(attempts)
        reasons: list[str] = []

        role = getattr(task, "role", None) or ""
        model, effort, source = self._default_for(role, agent)
        reasons.append(f"role {role or '(none)'} {source} {model}/{effort}")
        base_tier = _position(model, effort)
        tier = base_tier

        complexity = str(getattr(task, "complexity", Complexity.MEDIUM.value))
        floor = _FLOOR_BY_COMPLEXITY.get(complexity, 0)
        if floor > tier:
            reasons.append(f"complexity {complexity} raises the floor to {self._name(floor)}")
            tier = floor

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

        # The policy pair survives while nothing moved it. Reading the rung back
        # off the ladder would rewrite documentation's haiku/medium to haiku/low
        # — which is the bug that made the table unimplementable.
        if tier != base_tier:
            model, effort = self.ladder[tier]

        # Task pins are absolute. A person who wrote `model:` into the workflow
        # made a decision the router has no standing to overrule — including by
        # escalating past it.
        pinned = False
        pin_model = getattr(task, "model", None)
        pin_effort = getattr(task, "effort", None)
        if pin_model:
            pinned = True
            model = str(pin_model)
            pin_tier = tier_of(model)
            effort = str(pin_effort or self.ladder[pin_tier if pin_tier is not None else tier][1])
            tier = _position(model, effort)
            reasons = [f"task pins {model}/{effort} — the router does not overrule a task pin"]
            escalated_from = None
        elif pin_effort:
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

    def _default_for(self, role: str, agent: Any) -> tuple[str, str, str]:
        """The starting model and effort, and where it came from."""
        if role in self.policy:
            model, effort = self.policy[role]
            return model, effort, "routes to"
        charter_model = getattr(agent, "model", None) if agent is not None else None
        if charter_model:
            agent_id = getattr(agent, "id", "its charter")
            charter_effort = getattr(agent, "effort", None) or FALLBACK[1]
            return str(charter_model), str(charter_effort), f"is not in the policy; {agent_id} defaults to"
        return FALLBACK[0], FALLBACK[1], "is not in the policy and no charter names a model; falls back to"

    def _name(self, tier: int) -> str:
        m, e = self.ladder[max(0, min(tier, TOP))]
        return f"{m}/{e}"
