"""Recipe cost estimator for the D readiness gate.

Estimates the token budget a recipe will consume before it runs, using the
same ``count_tokens`` heuristic already applied per-step in the runner.

Budget components:
  1. ``est_arg_tokens`` — sum of arg-token estimates across all steps
     (renders the raw arg template dicts, not the final rendered strings —
     close enough for a pre-run gate where inputs aren't known yet).
  2. ``output_reserve_tokens`` — flat 256 tokens per step as an output
     buffer reserve (heuristic; unresolved from telemetry JSONL per the spec).
  3. ``author_est_tokens`` — the recipe's declared ``cost`` field
     (``recipe.cost``), representing the author's upfront estimate for
     the full model round-trip including output.  Defaults to 0 when absent.

``est_total_tokens = est_arg_tokens + output_reserve_tokens + author_est_tokens``

The cost cap is read from ``VISE_LOOP_COST_CAP`` (default 50_000 tokens).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from vise.recipes.loader import Recipe
from vise.recipes.telemetry import count_tokens

log = logging.getLogger(__name__)

_DEFAULT_OUTPUT_RESERVE_PER_STEP = 256
_DEFAULT_COST_CAP = 50_000


def cost_cap() -> int:
    """Return the configured cost cap from ``VISE_LOOP_COST_CAP`` (default 50 000)."""
    raw = os.environ.get("VISE_LOOP_COST_CAP", "")
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_COST_CAP
    except (ValueError, TypeError):
        return _DEFAULT_COST_CAP


def estimate_cost(
    recipe: Recipe,
    inputs: dict[str, Any] | None = None,
    *,
    output_reserve_per_step: int = _DEFAULT_OUTPUT_RESERVE_PER_STEP,
) -> dict[str, Any]:
    """Estimate token cost for *recipe* before execution.

    Args:
        recipe: The Recipe to estimate.
        inputs: Optional input dict (used for token-count context, not rendered).
        output_reserve_per_step: Flat reserve per step for model output
            (default 256).

    Returns a dict::

        {
            "est_arg_tokens":      <int>,   # sum of raw step-arg tokens
            "output_reserve_tokens": <int>, # steps * output_reserve_per_step
            "author_est_tokens":   <int>,   # recipe.cost (author declaration)
            "est_total_tokens":    <int>,   # sum of all three
            "cap":                 <int>,   # VISE_LOOP_COST_CAP
            "within_cap":          <bool>,  # est_total_tokens <= cap
        }
    """
    est_arg = sum(count_tokens(step.args) for step in recipe.steps)
    output_reserve = len(recipe.steps) * output_reserve_per_step
    author_est = recipe.cost if recipe.cost is not None else 0

    est_total = est_arg + output_reserve + author_est
    cap = cost_cap()

    return {
        "est_arg_tokens": est_arg,
        "output_reserve_tokens": output_reserve,
        "author_est_tokens": author_est,
        "est_total_tokens": est_total,
        "cap": cap,
        "within_cap": est_total <= cap,
    }
