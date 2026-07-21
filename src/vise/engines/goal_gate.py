"""Goal gate — pure logic for the Stop-hook autonomy lock.

When VISE_GOAL_GATE=1 and a goal is active, the Stop hook blocks the
agent's turn-end until the goal completes. This module computes
*whether* to block and *why* — the hook just translates the decision
into Claude Code's Stop-hook JSON protocol.

Safety rails (all opt-out via env):
  - max attempts cap (VISE_GOAL_GATE_MAX_ATTEMPTS, default 50)
  - plateau detection (N consecutive validator_runs with same confidence
    within tolerance → "stalled", let agent stop and ask for human help)
  - cancel file `.claude/state/goal-cancel` (touch it to unlock)
  - overall override env VISE_GOAL_GATE_OVERRIDE=1
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from vise.engines import goal_state


class Action(str, Enum):
    CONTINUE = "continue"
    GOAL_COMPLETE = "goal_complete"
    ROTATE = "rotate"
    WAIT_RESET = "wait_reset"


@dataclass
class Decision:
    action: Action
    confidence: float
    target_confidence: float
    advisory: str


@dataclass
class GateDecision:
    block: bool
    reason: str          # text shown to the agent (or telemetry note)
    cause: str           # short code: complete | abandoned | max_attempts | plateau |
                         #             cancelled | override | no_goal | continue |
                         #             rotate | wait_reset | gate_disabled


_PLATEAU_WINDOW = 5
_PLATEAU_TOLERANCE = 0.01
_DEFAULT_MAX_ATTEMPTS = 50


def _max_attempts() -> int:
    raw = os.environ.get("VISE_GOAL_GATE_MAX_ATTEMPTS", "")
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_MAX_ATTEMPTS
    except ValueError:
        return _DEFAULT_MAX_ATTEMPTS


def _plateau_window() -> int:
    raw = os.environ.get("VISE_GOAL_GATE_PLATEAU_WINDOW", "")
    try:
        v = int(raw)
        return v if v >= 2 else _PLATEAU_WINDOW
    except ValueError:
        return _PLATEAU_WINDOW


def _cancel_file(project_dir: str) -> Path:
    return Path(project_dir) / ".claude" / "state" / "goal-cancel"


def is_gate_enabled() -> bool:
    return os.environ.get("VISE_GOAL_GATE") == "1"


def is_overridden() -> bool:
    return os.environ.get("VISE_GOAL_GATE_OVERRIDE") == "1"


def _structured_confidence_series(goal: goal_state.Goal) -> list[float] | None:
    """Return the numeric confidence series from STRUCTURED ``confidence_update``
    history events, or ``None`` if no such events exist (caller falls back).

    Each ``confidence_update`` event stores the float verbatim in ``detail``
    (e.g. ``"0.420"``), written by ``goal_validate`` alongside the
    human-readable ``validator_run`` event. Reading the numeric here keeps
    plateau detection on structured state rather than parsing free text.
    """
    series: list[float] = []
    saw_event = False
    for h in goal.history:
        if h.event != "confidence_update":
            continue
        saw_event = True
        try:
            series.append(float(h.detail.strip()))
        except (ValueError, AttributeError):
            return None
    return series if saw_event else None


def detect_plateau(goal: goal_state.Goal, window: int | None = None) -> bool:
    """True if the last `window` confidence readings all sit within
    +/- _PLATEAU_TOLERANCE of each other — confidence has stopped moving.

    Prefers the structured ``confidence_update`` series; falls back to
    parsing legacy ``validator_run`` detail when no structured events exist.
    """
    w = window or _plateau_window()

    structured = _structured_confidence_series(goal)
    if structured is not None:
        recent = structured[-w:]
        if len(recent) < w:
            return False
        return (max(recent) - min(recent)) <= _PLATEAU_TOLERANCE

    # Fallback: parse legacy validator_run detail.
    runs = [h for h in goal.history if h.event == "validator_run"][-w:]
    if len(runs) < w:
        return False
    confidences: list[float] = []
    for h in runs:
        # detail looks like: "confidence=0.42 via 3 validators"
        try:
            piece = h.detail.split("confidence=", 1)[1].split()[0]
            confidences.append(float(piece))
        except (IndexError, ValueError):
            return False
    return (max(confidences) - min(confidences)) <= _PLATEAU_TOLERANCE


def _advisor_consult_reason(goal: "goal_state.Goal", decision: Decision) -> str:
    history = getattr(goal, "history", None) or []
    conf_hist: list[float] = []
    for ev in history[-_plateau_window():]:
        if getattr(ev, "event", "") != "confidence_update":
            continue
        # detail is either a bare float ("0.420") or "key=0.420".
        raw = getattr(ev, "detail", "").rsplit("=", 1)[-1].strip()
        try:
            conf_hist.append(float(raw))
        except ValueError:
            continue
    if not conf_hist:
        last_results = getattr(goal, "last_results", []) or []
        conf_hist = [
            float(getattr(r, "confidence_contribution", 0.0))
            for r in last_results[-_plateau_window():]
        ]
    payload = {
        "schema": "advisor-request.v1",
        "schema_version": "1",
        "reason": "plateau",
        "context_summary": (
            f"Goal '{goal.goal}' plateaued at confidence "
            f"{decision.confidence:.2f} after {goal.attempts} attempts."
        ),
        "recent_attempts": [f"run {i}: conf={c:.2f}" for i, c in enumerate(conf_hist, 1)],
        "relevant_files": [],
        "confidence_history": conf_hist,
    }
    return (
        "### Advisor consult payload\n"
        "Spawn the `advisor` agent (Task tool) with this `advisor-request.v1`:\n"
        f"```json\n{__import__('json').dumps(payload, indent=2)}\n```\n"
        "Route the returned `advisor-response.v1` per `decision` "
        "(PLAN → fresh wave, CORRECTION → re-brief prior worker, "
        "STOP → escalate to human)."
    )


def evaluate(project_dir: str, decision: Decision) -> GateDecision:
    """Decide whether to block the Stop hook given the current autonomy decision.

    Caller (the hook) must have already invoked `autonomy.decide()` once.
    """
    if not is_gate_enabled():
        return GateDecision(False, "", "gate_disabled")

    if is_overridden():
        return GateDecision(False, "VISE_GOAL_GATE_OVERRIDE=1", "override")

    if _cancel_file(project_dir).exists():
        return GateDecision(False, "cancel file present — gate released", "cancelled")

    goal = goal_state.get_goal(project_dir)
    if not goal or goal.status != goal_state.GoalStatus.ACTIVE.value:
        return GateDecision(False, "", "no_goal")

    if decision.action == Action.GOAL_COMPLETE:
        has_mechanical_pass = any(
            r.passed and getattr(r, "source", "mechanical") == "mechanical"
            for r in goal.last_results
        )
        if has_mechanical_pass:
            return GateDecision(False, "goal complete", "complete")
        # Confidence target hit but no mechanical validator actually passed —
        # this is fabricable, so keep blocking until a real pass exists.
        return GateDecision(
            True,
            "confidence target reached but no mechanical validator passed — "
            "run goal_validate.",
            "no_mechanical_pass",
        )

    max_att = _max_attempts()
    if goal.attempts >= max_att:
        return GateDecision(
            False,
            f"max attempts reached ({goal.attempts}/{max_att}) — gate released; "
            "goal needs human review",
            "max_attempts",
        )

    if detect_plateau(goal):
        consult_reason = _advisor_consult_reason(goal, decision)
        return GateDecision(
            False,
            f"confidence plateau over last {_plateau_window()} runs "
            f"(±{_PLATEAU_TOLERANCE}) — gate released; consult `advisor` agent "
            f"with `reason=plateau` before next attempt.\n\n{consult_reason}",
            "plateau",
        )

    # Goal still active and making progress — BLOCK.
    crit = "\n".join(f"- {c}" for c in goal.acceptance_criteria) or "(none specified)"
    reason = (
        f"## Goal gate active — DO NOT STOP\n\n"
        f"Goal: {goal.goal}\n"
        f"Confidence: {decision.confidence:.2f} / target {decision.target_confidence:.2f} "
        f"(attempt {goal.attempts}/{max_att})\n"
        f"Action hint: {decision.action.value} — {decision.advisory}\n\n"
        f"Acceptance criteria:\n{crit}\n\n"
        f"Continue working on the goal. Do not ask the user for input. "
        f"Use specialist subagents, run validators, fix failures. "
        f"Escape hatches: `goal_abandon` tool, "
        f"`touch .claude/state/goal-cancel`, or set VISE_GOAL_GATE_OVERRIDE=1."
    )
    cause = decision.action.value  # continue | rotate | wait_reset
    return GateDecision(True, reason, cause)
