"""A hook that fails open leaves a mark here, so the next session can say so.

Hooks fail open on purpose: one that raises takes the user's session down with
it, so a broad ``try/except`` around the outermost handler is the contract, not
sloppiness. What was missing is the other half. When that handler fired, the
experience went unrecorded, the blockers went unsurfaced and the snapshot went
untaken — and nobody was told. The user saw a session that worked.

This repository already refuses that collapse everywhere else. ``CLAUDE.md``:
"Absent and unreadable are different. A known absence fails a gate closed;
'could not tell' must report ``unverified``." A hook that swallowed an exception
is the 'could not tell' case, and until now it reported nothing at all.

Standard library only, and every function swallows its own errors, because this
is what runs when something else has already broken. Losing the note is the
acceptable failure; raising from here is not.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

#: Keep the most recent few. A hook that fails once has a bug; a hook that fails
#: two hundred times has the same bug, and the rest is a file that grows.
_CAP = 20

#: Older than this and the session it belonged to is long gone.
_TTL_SECONDS = 7 * 86400


def _path() -> Path:
    from vise.hooks import _xdg
    return _xdg.data_dir() / "hook_failures.json"


def note(hook: str, exc: BaseException) -> None:
    """Record that *hook* swallowed *exc*. Never raises, never blocks."""
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            entries = json.loads(path.read_bytes())
            if not isinstance(entries, list):
                entries = []
        except Exception:
            entries = []
        entries.append({
            "hook": hook,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "at": time.time(),
        })
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entries[-_CAP:]), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def drain() -> list[dict]:
    """Return the recent failures and forget them. Never raises.

    Draining is deliberate: the point is to tell someone once. A notice that
    reappears every session is one that gets ignored, which is the failure mode
    it exists to fix.
    """
    try:
        path = _path()
        entries = json.loads(path.read_bytes())
        path.unlink(missing_ok=True)
        if not isinstance(entries, list):
            return []
        cutoff = time.time() - _TTL_SECONDS
        return [e for e in entries
                if isinstance(e, dict) and float(e.get("at") or 0) >= cutoff]
    except Exception:
        return []


def summarise(entries: list[dict]) -> list[str]:
    """One line per hook that failed, worst-repeated first. Redacted."""
    if not entries:
        return []
    try:
        from vise.core.experience_rules import redact
    except Exception:
        def redact(text):  # noqa: ANN001, ANN202 — the notice matters more
            return text

    counts: dict[str, list] = {}
    for e in entries:
        hook = str(e.get("hook", "?"))
        counts.setdefault(hook, [0, ""])
        counts[hook][0] += 1
        counts[hook][1] = str(e.get("error", ""))

    lines = ["[vise] A hook failed open since the last session — it did not "
             "break anything, and it also did not do its job:"]
    for hook, (n, err) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        times = f" ({n}x)" if n > 1 else ""
        lines.append(f"- {hook}{times}: {redact(err)[:160]}")
    return lines


__all__ = ["drain", "note", "summarise"]
