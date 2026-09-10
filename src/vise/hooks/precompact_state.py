#!/usr/bin/env python3
"""PreCompact hook — carry the continuity thread across a compaction.

Reads graph state + goal state from disk (via hooks._common.read_active_state)
and this project's still-open failures (read_open_blockers). If a workflow or
goal is active, emits a systemMessage instructing the compact summarizer to
PRESERVE that thread. Silent (exit 0, no output) when there is nothing to say.
Fail-open on any exception.

The two halves are addressed differently on purpose. Workflow and goal state IS
in the conversation being summarized, so the instruction is "preserve this
verbatim". An open blocker comes off vise's own record and may never have been
mentioned, so it is offered as context the summary may need rather than as text
to keep — telling a summarizer to preserve a line that was never there is how
a summary acquires things that did not happen.

What is on that record is narrower than what a session learns. vise stores the
failure (a node gate that went red, a run that was blocked) and nothing that
would name a constraint someone discovered or an approach the session tried and
rejected; nothing writes those today, so nothing here can carry them.

PreCompact does NOT accept `hookSpecificOutput` — that key is only valid for
PreToolUse, UserPromptSubmit, PostToolUse, PostToolBatch, Stop, SubagentStop.
Claude Code rejects it here ("Hook JSON output validation failed"). The
actual state restore after compaction is handled by session_restore.py
(SessionStart, source=compact), which DOES support hookSpecificOutput. This
hook only needs to nudge the summarizer via a field PreCompact accepts.

Output schema:
    {"systemMessage": "<preserve instructions>"}
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
        from vise.hooks._common import read_active_state, read_open_blockers
        state = read_active_state(project_dir)
        blockers = read_open_blockers(project_dir)
        if not state and not blockers:
            return 0

        lines: list[str] = []
        if state:
            lines.append("IMPORTANT — preserve the following active vise state "
                         "verbatim in the summary:")
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
        if state:
            lines.append("- Next action: call graph_status to re-sync workflow "
                         "state, then continue from the current node.")

        if blockers:
            if lines:
                lines.append("")
            lines.append("Known unresolved failures on this project, from vise's "
                         "record. Keep whichever of these the session actually "
                         "touched; do not introduce the rest as things that "
                         "happened:")
            for entry in blockers:
                severity = str(entry.get("severity") or "medium")
                where = str(entry.get("file_pattern") or "?")
                what = str(entry.get("description") or "").strip()[:160]
                lines.append(f"- [{severity}] {where}: {what}")

        print(json.dumps({"systemMessage": "\n".join(lines)}))
    except Exception:
        return 0  # fail-open
    return 0


if __name__ == "__main__":
    sys.exit(main())
