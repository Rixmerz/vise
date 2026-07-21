#!/usr/bin/env python3
"""Stop hook — block turn-end while an active goal is unfinished.

Gated on VISE_GOAL_GATE=1 (default off). When enabled:
  - reads the active goal
  - reads goal state and builds a Decision for engines.goal_gate.evaluate()
  - delegates to engines.goal_gate.evaluate() for block/unblock + reason

Block protocol: print JSON {"decision":"block","reason":"..."} to stdout.
Claude Code re-invokes the agent with `reason` as instruction; the agent
cannot stop the turn until the gate releases.

Safety: every escape hatch (max attempts, plateau, cancel file, override
env) is enforced inside engines.goal_gate — this script is just glue.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if os.environ.get("VISE_GOAL_GATE") != "1":
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())

    try:
        from vise.engines import goal_gate
        from vise.engines.goal_gate import Action, Decision
    except Exception:
        return 0  # vise not importable — never block

    try:
        # Build a minimal "continue" decision; goal_gate.evaluate reads goal state itself.
        # Confidence/decision are derived from STRUCTURED goal fields
        # (goal.confidence / goal.last_results), never by string-parsing
        # history detail — the parsed string was fabricable (Hole B).
        from vise.engines import goal_state
        goal = goal_state.get_goal(project_dir)
        confidence = 0.0
        target = 1.0
        if goal:
            confidence = float(goal.confidence)
            if goal.target_confidence:
                target = goal.target_confidence
        action = Action.GOAL_COMPLETE if confidence >= target else Action.CONTINUE
        decision = Decision(action=action, confidence=confidence,
                            target_confidence=target, advisory="")
        gate = goal_gate.evaluate(project_dir, decision)
    except Exception as e:  # defensive: never deadlock on a hook bug
        print(f"# goal_gate hook error: {e}", file=sys.stderr)
        return 0

    if not gate.block:
        return 0

    print(json.dumps({"decision": "block", "reason": gate.reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
