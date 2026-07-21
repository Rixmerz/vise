#!/usr/bin/env python3
"""PostToolUse hook: immediate lint feedback after Edit/Write/MultiEdit.

Runs a ruff-only fast pass (no mypy — latency budget <2s) on the edited
file when it is Python, and prints a concise findings summary to stderr.
Pure feedback: never blocks, never raises, always exits 0.

Protocol:

- stdin:  ``{"tool_name": ..., "tool_input": {"file_path": ...}, ...}``
- stderr: up to ``MAX_LINES`` lines of ``severity code file:line message``.
- exit 0: always.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_LINES = 10
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd()


def _read_input() -> dict:
    try:
        data = sys.stdin.read()
        if not data:
            return {}
        return json.loads(data)
    except Exception:
        return {}


def main() -> int:
    payload = _read_input()
    if payload.get("tool_name", "") not in _EDIT_TOOLS:
        return 0

    file_path = (payload.get("tool_input", {}) or {}).get("file_path", "")
    if not isinstance(file_path, str) or not file_path.endswith(".py"):
        return 0
    if not Path(file_path).exists():
        return 0

    try:
        from vise.engines.lsp_diagnostics import lsp_diagnostics
        result = lsp_diagnostics(
            str(_project_dir()), file_path, tools=("ruff",)
        )
    except Exception:
        return 0

    if not result.get("available"):
        return 0
    diags = result.get("diagnostics") or []
    if not diags:
        return 0

    # Errors first, then warnings; cap the output.
    diags.sort(key=lambda d: 0 if d.get("severity") == "error" else 1)
    shown = diags[:MAX_LINES]
    rel = file_path
    try:
        rel = str(Path(file_path).resolve().relative_to(_project_dir()))
    except Exception:
        pass
    lines = [
        f"{d.get('severity', '?')} {d.get('code', '')} "
        f"{rel}:{d.get('line', 0)} {d.get('message', '')}".strip()
        for d in shown
    ]
    extra = len(diags) - len(shown)
    if extra > 0:
        lines.append(f"… and {extra} more")
    print("[vise.lint] " + "\n[vise.lint] ".join(lines), file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        raise SystemExit(0)
