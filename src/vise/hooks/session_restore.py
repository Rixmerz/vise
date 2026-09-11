#!/usr/bin/env python3
"""SessionStart hook — re-inject active workflow/goal state after
compact/resume/startup.

Reads graph state + goal state from disk (via hooks._common.read_active_state).
If active, emits a compact state block (< 15 lines) as additionalContext so
the fresh context knows the thread.

It also reports any hook that failed open since the last session, and that is
the only moment anyone finds out: hooks swallow their exceptions by contract, so
a broken one looks exactly like a working one until something it should have
recorded turns out to be missing. That report is drained FIRST and in its own
try, before the state read — putting it after meant a failure in reading the
state took the failure notice down with it, which is the same silence twice.

Silent when there is nothing of either kind. Fail-open on any exception.

Output schema:
    {"hookSpecificOutput": {"hookEventName": "SessionStart",
       "additionalContext": "<state block>"}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _emit(lines: list[str]) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(lines),
        }
    }))


def main() -> int:
    try:
        json.load(sys.stdin)  # consume hook input; source unused (all apply)
    except Exception:
        pass

    lines: list[str] = []
    try:
        from vise.hooks import _failsafe
        lines = _failsafe.summarise(_failsafe.drain())
    except Exception:
        pass

    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())
        from vise.hooks._common import read_active_state
        state = read_active_state(project_dir)
        if not state:
            if lines:
                _emit(lines)
            return 0

        if lines:
            lines.append("")
        lines.append("[vise] Active state restored from disk:")
        if state.get("workflow"):
            lines.append(f"- Workflow: {state['workflow']} @ node "
                         f"{state.get('current_node')}")
            if state.get("tools_blocked"):
                lines.append(f"- Blocked tools at this node: "
                             f"{', '.join(state['tools_blocked'])}")
            if state.get("pending_validators"):
                lines.append(f"- Pending validators: "
                             f"{json.dumps(state['pending_validators'])}")
        if state.get("goal"):
            lines.append(f"- Goal: {state['goal']} "
                         f"(confidence {state.get('goal_confidence')}/"
                         f"{state.get('goal_target')})")
        lines.append("- Call graph_status to re-sync before continuing.")

        _emit(lines)
    except Exception:
        # The state block is lost, but a failure notice already gathered above
        # is the one thing that must still get out.
        if lines:
            try:
                _emit(lines)
            except Exception:
                pass
        return 0  # fail-open
    return 0


if __name__ == "__main__":
    sys.exit(main())
