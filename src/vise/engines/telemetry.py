"""Orchestration telemetry — append-only JSONL event log.

Writes to ~/.local/share/vise/telemetry/orchestration.jsonl (or
$VISE_TELEMETRY_DIR/orchestration.jsonl when the env var is set).

Two logs, two shapes:

``orchestration.jsonl`` via ``record_intervention``
  workflow_prompt     — the suggester told the agent to pick a workflow

``runs.jsonl`` via ``record_run_event`` — the agent runtime
  run_started / dispatched / collected / verified / deferred / not_admitted /
  replanned / run_finished — one line per scheduler decision, so "why did this
  run cost $1.83" and "why did task 17 end up on opus" have an answer that does
  not depend on a session still being open.

``gates.jsonl`` via ``record_event`` — the workflow lifecycle
  workflow_activated  — a graph was activated, and which one
  node_gate_blocked   — a node's validators failed and the traverse was denied
  node_gate_overridden— VISE_NODE_GATE_OVERRIDE=1 walked through a red gate
  validator_outcome   — one validator ran (or skipped), with its outcome

Keep both lists equal to the ``_VALID_*`` frozensets below. ``record_intervention``
previously named four kinds the same change had already made illegal, so it
warned and dropped every one of them while the docs still advertised them.

WHY THIS EXISTS. vise gates other repos on evidence and could produce none about
itself. Nothing recorded which workflow ran, which gate blocked, or — the signal
that actually matters — whether anyone was setting the override and walking
through. ``node_gate_state`` counts failed attempts, but the same counter
advances whether the gate was fixed or bypassed, so it cannot answer the one
question worth asking about a gate.

Every write is best-effort and swallows OSError. Telemetry that can break a
session is worse than no telemetry.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from vise.core import paths as _paths

log = logging.getLogger(__name__)

# One real event. The previous four all belonged to the auto-activate classifier
# and the override detector that measured its false-positive rate — none of which
# ship anymore: a regex deciding the workflow was replaced by the model reading
# the request. An allowlist naming kinds nothing emits is not an allowlist, it is
# a wish list, so it shrinks with its producers.
_VALID_KINDS = frozenset({"workflow_prompt"})

# Gate-lifecycle kinds. Same rule as above: a kind with no producer does not
# belong here. Each of these is emitted from exactly one call site.
_VALID_EVENTS = frozenset({
    "workflow_activated",
    "node_gate_blocked",
    "node_gate_overridden",
    "validator_outcome",
})

# Agent-runtime kinds, written to their own log. Separate from gates.jsonl
# because the volume is different by an order of magnitude — one line per gate
# per traversal versus one per scheduler decision — and mixing them would make
# ``vise insights`` read a mostly-runtime file to answer a gate question.
#
# Same allowlist rule as above: a kind with no producer does not belong here.
# Every one of these is emitted from vise/runtime/scheduler.py.
_VALID_RUN_EVENTS = frozenset({
    "run_started",
    "dispatched",
    "collected",
    "verifying",
    "verified",
    "deferred",
    "not_admitted",
    "unroutable",
    "stalled",
    "replanned",
    "replan_declined",
    "replan_unavailable",
    "context_failed",
    "wall_clock_exhausted",
    "cancelled",
    "run_finished",
})


def _telemetry_dir() -> Path:
    base = os.environ.get("VISE_TELEMETRY_DIR")
    if base:
        return Path(base)
    return _paths.data_dir() / "telemetry"


def record_intervention(
    kind: str,
    prompt_hash: str,
    extra: dict | None = None,
) -> None:
    """Append one orchestration event to the JSONL log. Best-effort; never raises."""
    if kind not in _VALID_KINDS:
        log.warning("[telemetry] unknown kind %r — skipping", kind)
        return
    record = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "kind": kind,
        "prompt_hash": prompt_hash,
        "extra": extra or {},
    }
    try:
        out = _telemetry_dir()
        out.mkdir(parents=True, exist_ok=True)
        path = out / "orchestration.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        log.warning("[telemetry] failed to write orchestration event: %s", e)


def record_event(kind: str, **fields) -> None:
    """Append one gate-lifecycle event to ``gates.jsonl``. Never raises.

    Called from paths that already persist state, so it adds a file append and
    no new work. ``fields`` is written flat rather than nested under ``extra``:
    these records are read by ``vise insights`` with a dict lookup, and a nested
    envelope buys nothing at this size.
    """
    if kind not in _VALID_EVENTS:
        log.warning("[telemetry] unknown event %r — skipping", kind)
        return
    record = {"ts": datetime.now(tz=timezone.utc).isoformat(), "kind": kind, **fields}
    try:
        out = _telemetry_dir()
        out.mkdir(parents=True, exist_ok=True)
        with (out / "gates.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        log.warning("[telemetry] failed to write gate event: %s", e)


def record_run_event(run_id: str, kind: str, **fields) -> None:
    """Append one agent-runtime event to ``runs.jsonl``. Never raises.

    Best-effort, like every other writer here. The run's own ``state.json`` is
    the authoritative record — this log exists for the cross-run questions that
    one file cannot answer: which roles escalate most, what a task of this shape
    usually costs, whether the verifier ever disagrees.
    """
    if kind not in _VALID_RUN_EVENTS:
        log.warning("[telemetry] unknown run event %r — skipping", kind)
        return
    record = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "run_id": run_id,
        "kind": kind,
        **fields,
    }
    try:
        out = _telemetry_dir()
        out.mkdir(parents=True, exist_ok=True)
        with (out / "runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        log.warning("[telemetry] failed to write run event: %s", e)


def read_run_events(limit: int | None = None, run_id: str | None = None) -> list[dict]:
    """Read ``runs.jsonl`` oldest-first, optionally for one run.

    Same torn-final-line tolerance as ``read_events``: the log is appended from
    several threads and a crash mid-write must not make the reader useless.
    """
    path = _telemetry_dir() / "runs.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id is None or event.get("run_id") == run_id:
                events.append(event)
    except OSError as e:
        log.warning("[telemetry] failed to read run events: %s", e)
        return []
    return events[-limit:] if limit else events


def read_events(limit: int | None = None) -> list[dict]:
    """Read ``gates.jsonl`` oldest-first. A malformed line is skipped, not fatal.

    The log is append-only from several processes; a torn final line is expected
    after a crash and must never make the reader useless.
    """
    path = _telemetry_dir() / "gates.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as e:
        log.warning("[telemetry] failed to read gate events: %s", e)
        return []
    return events[-limit:] if limit else events
