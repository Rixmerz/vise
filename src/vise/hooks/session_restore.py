#!/usr/bin/env python3
"""SessionStart hook — re-inject active workflow/goal state after
compact/resume/startup.

Reads graph state + goal state from disk (via hooks._common.read_active_state).
If active, emits a compact state block (< 15 lines) as additionalContext so
the fresh context knows the thread. Silent when nothing is active.
Fail-open on any exception.

Output schema:
    {"hookSpecificOutput": {"hookEventName": "SessionStart",
       "additionalContext": "<state block>"}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        json.load(sys.stdin)  # consume hook input; source unused (all apply)
    except Exception:
        pass

    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())
        from vise.hooks._common import read_active_state
        state = read_active_state(project_dir)
        if not state:
            return 0

        lines = ["[vise] Active state restored from disk:"]
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

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "\n".join(lines),
            }
        }))
    except Exception:
        return 0  # fail-open
    return 0


if __name__ == "__main__":
    sys.exit(main())
