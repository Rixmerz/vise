"""Orchestration telemetry — append-only JSONL event log.

Writes to ~/.local/share/vise/telemetry/orchestration.jsonl (or
$VISE_TELEMETRY_DIR/orchestration.jsonl when the env var is set).

Supported event kinds:
  auto_activate_hit   — workflow auto-activated from intent classifier
  auto_activate_miss  — intent matched but activation skipped / suggestion only
  pre_plan_emit       — auto_pre_plan injection fired
  user_override       — user reset/switched off an auto-activated workflow within 30m
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_VALID_KINDS = frozenset(
    {"auto_activate_hit", "auto_activate_miss", "pre_plan_emit", "user_override"}
)


def _telemetry_dir() -> Path:
    base = os.environ.get("VISE_TELEMETRY_DIR")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share" / "vise" / "telemetry"


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
