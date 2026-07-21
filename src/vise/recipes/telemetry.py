"""Per-step JSONL telemetry, token counting, and budget enforcement for recipes.

Token counting uses a fast heuristic (len(s) // 4) — not tiktoken — to avoid
external dependencies. It intentionally underestimates for non-ASCII text but
is accurate enough for budget enforcement purposes.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(value: object) -> int:
    """Estimate token count for an arbitrary value using a fast heuristic.

    Heuristic: strings use max(1, len(s) // 4), which approximates the GPT
    tokenizer's average of ~4 chars/token for English text. For dicts and
    lists the function recurses over keys and values. Other scalars are
    converted to str first. This is intentionally approximate and fast — it
    adds no external dependency and is suitable for budget gating, not billing.
    """
    if isinstance(value, str):
        return max(1, len(value) // 4)
    if isinstance(value, dict):
        total = 0
        for k, v in value.items():
            total += count_tokens(k) + count_tokens(v)
        return total
    if isinstance(value, list):
        return sum(count_tokens(item) for item in value)
    # int, float, bool, None, etc.
    return max(1, len(str(value)) // 4)


# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------

class StepTelemetryWriter:
    """Appends per-step records as JSONL to <state_dir>/recipes/<recipe>/<run_id>.jsonl."""

    def __init__(
        self,
        state_dir: str | Path,
        recipe_name: str,
        run_id: str | None = None,
    ) -> None:
        self._run_id = run_id or uuid4().hex[:12]
        out_dir = Path(state_dir) / "recipes" / recipe_name
        out_dir.mkdir(parents=True, exist_ok=True)
        self._path = out_dir / f"{self._run_id}.jsonl"

    @property
    def path(self) -> Path:
        return self._path

    @property
    def run_id(self) -> str:
        return self._run_id

    def write(self, record: dict) -> None:
        """Append one JSON line to the telemetry file. Best-effort; swallows OSError."""
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError as e:
            log.warning("[telemetry] failed to write record: %s", e)


# ---------------------------------------------------------------------------
# Budget tracker
# ---------------------------------------------------------------------------

class BudgetTracker:
    """Tracks cumulative arg tokens and halts when a cap is exceeded."""

    def __init__(self, max_tokens: int | None) -> None:
        self._max = max_tokens
        self._total = 0

    def add(self, n: int) -> None:
        self._total += n

    def check(self) -> str | None:
        """Return an error string if the budget is exceeded, else None."""
        if self._max is not None and self._total > self._max:
            return f"token budget exceeded: {self._total} > {self._max}"
        return None

    @property
    def total(self) -> int:
        return self._total


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------

def redact_for_telemetry(args: dict) -> dict:
    """Return a copy of args with env refs replaced by their literal {{ env.X }} token."""
    from vise.recipes.renderer import redact_env_refs
    return redact_env_refs(args)
