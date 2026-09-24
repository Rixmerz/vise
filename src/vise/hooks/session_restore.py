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

And it says when tasky's ledger holds earlier sessions of this repo. vise's
own memory is short lessons keyed to a file glob; what an earlier session
*decided* lives in the transcript, and tasky keeps the transcript, searchable,
with the fixes that were tried and failed. Its own SessionStart note is about
the tasks it queued, not about what can be recalled, so without this line the
ledger is a store nobody is told about. Read off the database, never asked: a
file cannot say whether tasky's tools are connected to this session, so the
line says "if they are".

Silent when there is nothing of any kind. Fail-open on any exception.

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


def _ledger_lines(project_dir: str) -> list[str]:
    """The recall note, when this repo has sessions to recall. Else nothing.

    Three lines and no more: the ledger is a resource, not a phase, and a
    session that never asks "what did we decide" should not pay for it. A
    ledger that holds only other repositories says nothing here either — the
    tools default to this repo, and would find nothing.
    """
    from vise.core.neighbour_state import ledger_state

    ledger = ledger_state(project_dir)
    if not ledger.present:
        return []
    return [
        f"[vise] {ledger.detail}.",
        "- If the tasky tools are connected: before deciding something an earlier "
        "session may already have settled, search_history(query=<a few keywords>) for "
        "problems and the fixes that failed, search_conversations(query=<a few "
        "keywords>) for what was said, and quote the hit with its session and date; "
        "last_session for the last hand-off. Not on greenfield edits.",
        "- If they are not connected, say so rather than answering from memory.",
    ]


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

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd())
    try:
        lines += _ledger_lines(project_dir)
    except Exception as exc:
        # Its own try: a tasky ledger that cannot be read must not take the
        # state block down, and the failsafe ledger is what says it happened.
        try:
            from vise.hooks import _failsafe
            _failsafe.note("session_restore.tasky", exc)
        except Exception:
            pass

    try:
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
