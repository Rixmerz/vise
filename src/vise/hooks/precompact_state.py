#!/usr/bin/env python3
"""PreCompact hook — tell the summarizer to preserve active workflow/goal state.

Reads graph state + goal state from disk (via hooks._common.read_active_state).
If a workflow or goal is active, emits additionalContext instructing the
compact summarizer to PRESERVE the continuity thread. Silent (exit 0, no
output) when nothing is active. Fail-open on any exception.

Output schema:
    {"hookSpecificOutput": {"hookEventName": "PreCompact",
       "additionalContext": "<preserve instructions>"}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        json.load(sys.stdin)  # consume hook input; content unused
    except Exception:
        pass

    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())
        from vise.hooks._common import read_active_state
        state = read_active_state(project_dir)
        if not state:
            return 0

        lines = ["IMPORTANT — preserve the following active vise state "
                 "verbatim in the summary:"]
        if state.get("workflow"):
            lines.append(f"- Active workflow: {state['workflow']} "
                         f"(current node/phase: {state.get('current_node')})")
            if state.get("pending_validators"):
                lines.append(f"- Pending validators: "
                             f"{json.dumps(state['pending_validators'])}")
        if state.get("goal"):
            lines.append(f"- Active goal: {state['goal']} "
                         f"(confidence {state.get('goal_confidence')}/"
                         f"{state.get('goal_target')})")
        lines.append("- Next action: call graph_status to re-sync workflow "
                     "state, then continue from the current node.")

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreCompact",
                "additionalContext": "\n".join(lines),
            }
        }))
    except Exception:
        return 0  # fail-open
    return 0


if __name__ == "__main__":
    sys.exit(main())
