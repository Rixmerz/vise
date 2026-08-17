#!/usr/bin/env python3
"""PreToolUse gate: read this repo by symbols, not by paths.

A well-factored repo costs an agent *more* to read than a badly-factored one —
more files, more jumps, more context burned — so structure and navigability
pull against each other and the agent resolves the tension by writing coupled
code. The read tools remove that tension; this hook is what makes the agent
reach for them instead of falling back to `Read` out of habit.

Three things this hook is built around, each of which is the difference
between a gate people keep and a gate people switch off:

**The denial message carries the replacement call, already formed.** A deny
that only says "no" gets routed around — the agent tries `cat`, then `sed -n`,
then writes the helper it could not find. The message is the teaching surface,
not the wall.

**Warning mode is the default.** `VISE_CODELAYER=warn` records what it *would*
have denied and lets everything through, so the false-positive rate can be
measured on a real week of work before anything blocks. `off` (the default of
the default) does nothing at all. Only `enforce` denies.

**The kill switch exists from the first line.** `VISE_CODELAYER=off`, and the
hook is inert. A gate that can lock you out of fixing the gate is a gate that
gets uninstalled the first time it misfires — and dogfooding this one means
its bugs land on the person least able to route around them.

Protocol
--------
- stdin:  ``{"tool_name": ..., "tool_input": {...}}``
- stdout: PreToolUse JSON decision. Deny uses both `decision: block` and
  `hookSpecificOutput.permissionDecision`, mirroring `graph_enforcer` — see the
  comment there for why both channels are load-bearing.
- exit 0: always. A crashing gate must never brick a session.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MODE_ENV = "VISE_CODELAYER"
SCOPE_ENV = "VISE_CODELAYER_SCOPE"
_MODES = ("off", "warn", "enforce")

# Read-by-path is only replaced where a symbol index can answer instead.
# Everything else — configs, migrations, docs, lockfiles — stays readable,
# because a gate that blocks `package.json` is a gate that gets turned off
# within the hour.
DEFAULT_SCOPE = ("src/",)

_ALWAYS_ALLOWED = re.compile(
    r"(^|/)("
    r"package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|"
    r"pyproject\.toml|setup\.cfg|requirements[^/]*\.txt|uv\.lock|poetry\.lock|"
    r"go\.(mod|sum)|Cargo\.(toml|lock)|Gemfile(\.lock)?|composer\.(json|lock)|"
    r"tsconfig[^/]*\.json|[^/]*\.ya?ml|[^/]*\.toml|[^/]*\.ini|[^/]*\.cfg|"
    r"Dockerfile|Makefile|[^/]*\.md|[^/]*\.txt|\.env[^/]*|[^/]*\.sql"
    r")$"
)

# Directories whose contents are read as text, not as a symbol graph.
_ALWAYS_ALLOWED_DIRS = ("tests/", "test/", "docs/", "migrations/", "__tests__/")

# Shell readers worth intercepting. Coverage is deliberately partial: there is
# no way to enumerate every path from a shell to a file's bytes, so the common
# ones are caught and the rest is accepted leakage rather than pretended away.
_SHELL_READERS = frozenset({
    "cat", "head", "tail", "less", "more", "bat", "sed", "awk", "nl", "od",
    "strings", "grep", "rg", "ag", "ack",
})


def _mode() -> str:
    raw = os.environ.get(MODE_ENV, "off").strip().lower()
    return raw if raw in _MODES else "off"


def _scope() -> tuple[str, ...]:
    raw = os.environ.get(SCOPE_ENV, "").strip()
    if not raw:
        return DEFAULT_SCOPE
    return tuple(p.strip().rstrip("/") + "/" for p in raw.split(",") if p.strip())


def _project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env).expanduser().resolve() if env else Path.cwd()


def _read_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _relative(path: str, project: Path) -> str:
    """Path as the index would name it: relative to the project, forward slashes."""
    p = path.strip().strip("'\"")
    if not p:
        return ""
    try:
        candidate = Path(p)
        if candidate.is_absolute():
            return str(candidate.resolve().relative_to(project)).replace("\\", "/")
    except (ValueError, OSError):
        return p.replace("\\", "/")
    return p.lstrip("./").replace("\\", "/")


def in_scope(path: str, project: Path, scope: tuple[str, ...]) -> bool:
    """Is this a path the symbol index can answer for, instead of the file?"""
    rel = _relative(path, project)
    if not rel:
        return False
    if _ALWAYS_ALLOWED.search(rel):
        return False
    if any(seg in rel for seg in _ALWAYS_ALLOWED_DIRS):
        return False
    return any(rel.startswith(prefix) for prefix in scope)


def _paths_in_command(cmd: str) -> list[str]:
    """Path-looking arguments of a shell reader, if the command is one."""
    tokens = cmd.split()
    if not tokens:
        return []
    # `sed -n '1,50p' src/x.py` and `grep -rn foo src/` both matter; the reader
    # can also appear after a pipe, which is how the first version was evaded.
    out: list[str] = []
    segment: list[str] = []
    for tok in [*tokens, "|"]:
        if tok in ("|", "&&", "||", ";"):
            if segment and Path(segment[0]).name in _SHELL_READERS:
                out += [t for t in segment[1:] if not t.startswith("-") and "/" in t]
            segment = []
        else:
            segment.append(tok)
    return out


def _teaching_message(paths: list[str], mode: str) -> str:
    target = paths[0] if paths else "this file"
    stem = Path(target).stem
    verb = "Denied" if mode == "enforce" else "Would deny"
    return (
        f"{verb}: reading `{target}` by path. This repo is read by symbol, "
        f"which costs a fraction of the tokens and comes with the callee "
        f"signatures and type definitions already attached.\n\n"
        f"  locate(\"{stem}\")                 -> candidates, no bodies\n"
        f"  read_unit(qname=\"<id>\")         -> body + contract closure\n"
        f"  resolve_location(path, line)    -> for a stack-trace line\n\n"
        f"Config, tests, docs, migrations and manifests are not gated — read "
        f"those normally. Set {MODE_ENV}=off to disable this gate entirely."
    )


def _decide(payload: dict, project: Path, scope: tuple[str, ...]) -> list[str]:
    """Which in-scope paths this call would read. Empty means nothing to gate."""
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}

    if tool in ("Read", "Glob"):
        p = ti.get("file_path") or ti.get("path") or ti.get("pattern") or ""
        return [p] if isinstance(p, str) and in_scope(p, project, scope) else []

    if tool == "Grep":
        p = ti.get("path") or ""
        return [p] if isinstance(p, str) and in_scope(p, project, scope) else []

    if tool == "Bash":
        cmd = ti.get("command", "")
        if not isinstance(cmd, str):
            return []
        return [p for p in _paths_in_command(cmd) if in_scope(p, project, scope)]

    return []


def _log_warning(project: Path, payload: dict, paths: list[str]) -> None:
    """Append what enforce mode *would* have denied.

    The whole point of warning mode is measuring the false-positive rate on a
    real week of work before anything blocks — a rate that cannot be guessed,
    only observed. Never raises: a full disk must not turn a warning into a
    crash.
    """
    try:
        d = project / ".vise"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "codelayer-warnings.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "tool": payload.get("tool_name", ""),
                "paths": paths[:5],
            }) + "\n")
    except Exception:
        pass


def main() -> int:
    approve = json.dumps({"decision": "approve"})
    mode = _mode()
    if mode == "off":
        print(approve)
        return 0

    try:
        payload = _read_input()
        project = _project_dir()
        paths = _decide(payload, project, _scope())

        if not paths:
            print(approve)
            return 0

        if mode == "warn":
            _log_warning(project, payload, paths)
            print(
                f"[vise.codelayer] {_teaching_message(paths, mode)}",
                file=sys.stderr,
            )
            print(approve)
            return 0

        reason = _teaching_message(paths, mode)
        print(json.dumps({
            "decision": "block",
            "message": reason,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }))
        return 0

    except Exception as exc:
        # Fail open, and say so. A gate that dies silently leaves the user
        # believing their reads are being routed through the index when they
        # are not — same contract as graph_enforcer.
        print(
            f"[vise.codelayer] approving without gating — gate error: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        print(approve)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print(json.dumps({"decision": "approve"}))
