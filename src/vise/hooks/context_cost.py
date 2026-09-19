#!/usr/bin/env python3
"""PostToolUse hook — what a tool result cost the context window, measured.

Every tool result enters the conversation and stays there until compaction. A
``cat`` of a lockfile, a ``Read`` of a 4 000-line module, a ``Grep`` with no
``-l`` — each is paid for on every turn that follows, and nothing in Claude
Code says how much was just spent. context-mode made the case for this and
answered it with prompting: a routing block that tells the model to prefer a
sandbox, and a per-call "bytes avoided" that is a hardcoded guess made *before*
the tool runs. This hook takes the half of the idea that can be measured.

PostToolUse receives the actual ``tool_response``. Its size is the fact. The
hook keeps one ledger per session under vise's data dir and says something in
two cases only:

- a single result over ``VISE_CONTEXT_CALL_KB`` (default 32), with the bounded
  form of the same call — at most three times per tool per session, because
  the fourth time it is the agent's decision;
- the session's running total crossing a multiple of
  ``VISE_CONTEXT_SESSION_KB`` (default 256), once per multiple, with the
  breakdown by tool.

Otherwise it is silent. It never blocks and never rewrites a result — Claude
Code hooks cannot — so the only lever it has is one line of context, and a
line that fires on every call would be the cost it exists to report.

Protocol:
  stdin:  {"tool_name": ..., "tool_response": ..., "session_id": ...}
  stdout: {"hookSpecificOutput": {"hookEventName": "PostToolUse",
           "additionalContext": "..."}} when there is something to say
  exit 0: always. Fail-open; a failure is noted to hooks/_failsafe.

Standard library only, imports at the top kept to what the silent path needs:
this runs after every Bash, Read, Grep, Glob and WebFetch.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Tools whose result the hook measures. Edit/Write results are tiny and the
#: MCP tools are somebody else's to account for.
MEASURED_TOOLS: frozenset[str] = frozenset({
    "Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch",
})

DEFAULT_CALL_KB = 32
DEFAULT_SESSION_KB = 256
#: The two knobs, as constants so `test_env_var_docs_sync` can see they are
#: read: a single result over the first is named; the session total crossing
#: each multiple of the second is reported.
CALL_KB_ENV_VAR = "VISE_CONTEXT_CALL_KB"
SESSION_KB_ENV_VAR = "VISE_CONTEXT_SESSION_KB"
#: Per-tool nudges per session. Three, then silence: the point was made.
MAX_CALL_NOTES_PER_TOOL = 3

#: The bounded form of each call, named rather than described — the agent
#: acts on a command it can copy, not on advice to be careful.
BOUNDED_FORMS: dict[str, str] = {
    "Bash": "pipe it through `| head -n 60` or `| tail -n 60`, or `| wc -l` when the count is the answer",
    "Read": "pass `offset` and `limit`, or read the symbol with `read_unit(qname)` where livespec is indexed",
    "Grep": "use `output_mode: files_with_matches` or `-l`, or add `head_limit`",
    "Glob": "narrow the pattern or the path",
    "WebFetch": "ask the fetch prompt for the one fact you need rather than the page",
    "WebSearch": "narrow the query",
}


def _kb(env: str, default: int) -> int:
    try:
        value = int(os.environ.get(env, "") or default)
    except ValueError:
        value = default
    return max(1, value)


def _size_of(response: object) -> int:
    """Bytes the result occupies as text. Measured, never estimated."""
    if response is None:
        return 0
    if isinstance(response, (bytes, bytearray)):
        return len(response)
    if isinstance(response, str):
        return len(response.encode("utf-8", errors="replace"))
    try:
        return len(json.dumps(response, ensure_ascii=False).encode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return len(str(response).encode("utf-8", errors="replace"))


def _ledger_path(session_id: str) -> Path:
    from vise.hooks import _xdg

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id) or "unknown"
    return _xdg.data_dir() / "context_cost" / f"{safe}.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def account(ledger: dict, tool: str, size: int, *, call_kb: int, session_kb: int) -> list[str]:
    """Fold one result into *ledger* and return the lines worth saying.

    Pure apart from the ledger it mutates, so a test can drive it without a
    filesystem. The ledger holds aggregates only — totals, per-tool totals,
    how many notes each tool has had, the last session milestone reported and
    the five largest calls — so it stays a few hundred bytes however long the
    session runs.
    """
    by_tool = ledger.setdefault("by_tool", {})
    by_tool[tool] = int(by_tool.get(tool, 0)) + size
    ledger["total"] = int(ledger.get("total", 0)) + size
    ledger["calls"] = int(ledger.get("calls", 0)) + 1
    largest = ledger.setdefault("largest", [])
    largest.append({"tool": tool, "bytes": size})
    largest.sort(key=lambda c: -int(c.get("bytes", 0)))
    del largest[5:]

    lines: list[str] = []
    notes = ledger.setdefault("notes", {})
    if size >= call_kb * 1024 and int(notes.get(tool, 0)) < MAX_CALL_NOTES_PER_TOOL:
        notes[tool] = int(notes.get(tool, 0)) + 1
        bound = BOUNDED_FORMS.get(tool, "narrow the call")
        lines.append(
            f"[vise] That {tool} result was {size // 1024} KB and stays in context "
            f"until compaction. If the next one can be smaller: {bound}."
        )

    milestone_bytes = session_kb * 1024
    reached = int(ledger["total"]) // milestone_bytes
    if reached > int(ledger.get("milestone", 0)):
        ledger["milestone"] = reached
        breakdown = ", ".join(
            f"{name} {int(n) // 1024} KB"
            for name, n in sorted(by_tool.items(), key=lambda kv: -int(kv[1]))
            if int(n) >= 1024
        )
        lines.append(
            f"[vise] Tool results have put {int(ledger['total']) // 1024} KB into this "
            f"session's context over {int(ledger['calls'])} calls"
            + (f" ({breakdown})" if breakdown else "")
            + ". Prefer bounded calls; a compaction will drop what is not in the "
            "workflow state."
        )
    return lines


def _emit(lines: list[str]) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(lines),
        }
    }))


def main() -> int:
    try:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            return 0
        if not isinstance(payload, dict):
            return 0
        tool = str(payload.get("tool_name") or "")
        if tool not in MEASURED_TOOLS:
            return 0
        size = _size_of(payload.get("tool_response"))
        if size <= 0:
            return 0
        session_id = str(payload.get("session_id") or "unknown")
        path = _ledger_path(session_id)
        ledger = _load(path)
        lines = account(
            ledger, tool, size,
            call_kb=_kb(CALL_KB_ENV_VAR, DEFAULT_CALL_KB),
            session_kb=_kb(SESSION_KB_ENV_VAR, DEFAULT_SESSION_KB),
        )
        _save(path, ledger)
        if lines:
            _emit(lines)
        return 0
    except Exception as exc:
        try:
            from vise.hooks import _failsafe
            _failsafe.note("context_cost", exc)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
