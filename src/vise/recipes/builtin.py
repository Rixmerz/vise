"""Built-in capability implementations.

meta.assert: inline assertion step that doesn't need an external MCP.
Supported conditions:
  - no_match: pattern must NOT appear in *against*
  - match: pattern must appear in *against*
"""
from __future__ import annotations

import re


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

    compiled = re.compile(pattern)

    if condition == "match":
        passed = any(compiled.search(t) for t in texts)
        if not passed:
            raise AssertionError(
                f"meta.assert failed: pattern {pattern!r} did not match any of {texts!r}"
            )
    elif condition == "no_match":
        matched = [t for t in texts if compiled.search(t)]
        if matched:
            raise AssertionError(
                f"meta.assert failed: pattern {pattern!r} matched {matched!r} but expected no_match"
            )
    else:
        raise ValueError(f"meta.assert: unknown condition {condition!r} (expected match|no_match)")

    return {"passed": True}
