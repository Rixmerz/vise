"""Built-in capability implementations.

meta.assert: inline assertion step that doesn't need an external MCP.
Supported conditions:
  - no_match: pattern must NOT appear in *against*
  - match: pattern must appear in *against*
"""
from __future__ import annotations

import json
import subprocess
import sys


#: A pattern longer than this is a mistake or an attack, not an assertion.
MAX_PATTERN_CHARS = 1000

#: How long the whole match may take. A gate step is allowed to be slow; it is
#: not allowed to be unbounded.
MATCH_TIMEOUT_S = 5

#: Run in a child process, matching one pattern against JSON-encoded texts.
_MATCH_PROGRAM = """
import json, re, sys
spec = json.loads(sys.stdin.read())
rx = re.compile(spec["pattern"])
json.dump([bool(rx.search(t)) for t in spec["texts"]], sys.stdout)
"""


def _search_all(pattern: str, texts: list[str]) -> list[bool]:
    """Which of ``texts`` the pattern matches. Bounded by a wall clock.

    In a subprocess, which is heavier than it looks necessary and is the only
    thing that actually works. Both the pattern and the text come from a recipe,
    and a recipe comes from the repository — project scope is loaded last, so a
    repo-supplied file fully replaces a bundled one of the same name. A
    catastrophically backtracking pair like ``(a+)+$`` against forty ``a``s runs
    for minutes.

    A thread would not help: Python cannot cancel one, and `re` does not release
    the GIL while matching, so a spinning match blocks the interpreter — the MCP
    server's event loop included, for every session using it. `asyncio.wait_for`
    around `to_thread` would return on time and leave the thread burning a core
    forever.

    A timeout is a failed assertion, not a crash: the step reports what it could
    not evaluate, which is what a gate is for.
    """
    payload = json.dumps({"pattern": pattern, "texts": texts})
    try:
        done = subprocess.run(
            [sys.executable, "-c", _MATCH_PROGRAM],
            input=payload, capture_output=True, text=True,
            timeout=MATCH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"meta.assert refused: pattern {pattern!r} did not finish within "
            f"{MATCH_TIMEOUT_S}s against {len(texts)} value(s) — it backtracks"
        ) from exc
    except OSError as exc:
        raise AssertionError(f"meta.assert could not run: {exc}") from exc
    if done.returncode != 0:
        raise AssertionError(
            f"meta.assert refused: pattern {pattern!r} is not a usable regex "
            f"({done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'no detail'})"
        )
    try:
        return [bool(x) for x in json.loads(done.stdout)]
    except ValueError as exc:
        raise AssertionError(f"meta.assert produced no verdict: {exc}") from exc


def meta_assert(args: dict) -> dict:
    """Execute a meta.assert step.

    Args dict keys:
        condition: "match" | "no_match"
        pattern:   regex string
        against:   str or list[str] to test

    Returns {"passed": True} or raises AssertionError.
    """
    condition = args.get("condition", "match")
    pattern = args.get("pattern", "")
    against = args.get("against", "")

    texts = [str(x) for x in against] if isinstance(against, list) else [str(against)]

    if len(pattern) > MAX_PATTERN_CHARS:
        raise AssertionError(
            f"meta.assert refused: pattern is {len(pattern)} characters, over "
            f"the {MAX_PATTERN_CHARS} limit"
        )

    hits = _search_all(pattern, texts)

    if condition == "match":
        passed = any(hits)
        if not passed:
            raise AssertionError(
                f"meta.assert failed: pattern {pattern!r} did not match any of {texts!r}"
            )
    elif condition == "no_match":
        matched = [t for t, hit in zip(texts, hits) if hit]
        if matched:
            raise AssertionError(
                f"meta.assert failed: pattern {pattern!r} matched {matched!r} but expected no_match"
            )
    else:
        raise ValueError(f"meta.assert: unknown condition {condition!r} (expected match|no_match)")

    return {"passed": True}
