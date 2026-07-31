"""Orchestration telemetry — append-only JSONL event log.

Writes to ~/.local/share/vise/telemetry/orchestration.jsonl (or
$VISE_TELEMETRY_DIR/orchestration.jsonl when the env var is set).

Supported event kinds:
  workflow_prompt     — the suggester told the agent to pick a workflow

Keep this list equal to ``_VALID_KINDS`` below. It previously named four kinds
that the same change had already made illegal, so ``record_intervention``
warned and dropped every one of them while the docs still advertised them.
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
