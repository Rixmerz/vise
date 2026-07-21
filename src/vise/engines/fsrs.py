"""Unified FSRS-style retrievability for the vise memory and experience stores.

Standard forgetting curve (FSRS approximation, no external dependency):

    R(t, S) = (1 + t / (9 * S)) ** -1

where:
    t  — days elapsed since the last review (float ≥ 0)
    S  — stability in days: the interval at which R ≈ 0.9

Properties:
    R(0, S)    = 1.0   (just reviewed → perfect recall)
    R(S, S)    ≈ 0.9   (at the stability horizon)
    R(9*S, S)  = 0.5   (half-life is 9 * S)
    monotonically decreasing in t, increasing in S

Default stability (DEFAULT_STABILITY_DAYS) is chosen so that the
90-day half-life used by the previous experience_gc model is preserved:
    9 * S ≈ 90  →  S ≈ 10

This file is intentionally small — import and call retrievability().
"""
from __future__ import annotations

from datetime import datetime, timezone

# Default stability in days.
# 9 * DEFAULT_STABILITY_DAYS ≈ 90 → half-life matches old exponential model.
DEFAULT_STABILITY_DAYS: float = 10.0

# Multiplicative bump applied to stability on each recall event.
# Simple rule: stability grows 20% per acknowledged recall.
STABILITY_BUMP: float = 1.2

# Minimum retrievability threshold for GC archiving.
# Entries below this are candidates for removal (experience GC uses its own
# threshold; this constant is for the memory store GC).
ARCHIVE_THRESHOLD: float = 0.10


def retrievability(t_days: float, stability: float = DEFAULT_STABILITY_DAYS) -> float:
    """Return the FSRS retrievability R ∈ (0, 1].

    Args:
        t_days:    Days since the last review (clamped to ≥ 0).
        stability: Stability in days (must be > 0; defaults to DEFAULT_STABILITY_DAYS).

    Returns:
        float in (0, 1]; 1.0 when t_days == 0.
    """
    if stability <= 0:
        stability = DEFAULT_STABILITY_DAYS
    t = max(0.0, t_days)
    return (1.0 + t / (9.0 * stability)) ** -1


def days_since(iso_timestamp: str) -> float:
    """Return elapsed days from *iso_timestamp* to now (UTC).

    Returns DEFAULT_STABILITY_DAYS * 9 (half-life) on parse errors so
    old/corrupt entries score conservatively rather than crashing.
    """
    if not iso_timestamp:
        return DEFAULT_STABILITY_DAYS * 9.0
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - dt).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return DEFAULT_STABILITY_DAYS * 9.0


if __name__ == "__main__":
    # Inline self-check — run with: python -m vise.engines.fsrs

    # 1. Monotonic decay: R at t=0 > R at t=S > R at t=9S
    r0 = retrievability(0.0)
    r_s = retrievability(DEFAULT_STABILITY_DAYS)
    r_half = retrievability(9.0 * DEFAULT_STABILITY_DAYS)
    assert r0 == 1.0, f"R(0) must be 1.0, got {r0}"
    assert r0 > r_s > r_half > 0, f"Curve not monotonic: {r0} {r_s} {r_half}"
    assert abs(r_half - 0.5) < 0.01, f"Half-life not ≈ 0.5, got {r_half}"

    # 2. Recall bump: higher stability → higher retrievability at same t
    t_test = DEFAULT_STABILITY_DAYS * 2
    r_before = retrievability(t_test, DEFAULT_STABILITY_DAYS)
    r_after = retrievability(t_test, DEFAULT_STABILITY_DAYS * STABILITY_BUMP)
    assert r_after > r_before, (
        f"Recall bump should raise retrievability at same t: {r_before} → {r_after}"
    )

    # 3. Old (fresh) entry vs un-recalled ancient entry
    r_fresh = retrievability(1.0)   # reviewed yesterday
    r_stale = retrievability(300.0)  # reviewed 300 days ago
    assert r_fresh > r_stale, (
        f"Fresh entry should score higher than stale: {r_fresh} vs {r_stale}"
    )

    # 4. days_since with empty string is safe
    d = days_since("")
    assert d >= 0, f"days_since('') should be non-negative, got {d}"

    print("fsrs self-check: all assertions passed")
    print(f"  R(0)   = {r0:.4f}")
    print(f"  R(S)   = {r_s:.4f}  (stability horizon, expect ≈0.909)")
    print(f"  R(9S)  = {r_half:.4f}  (half-life, expect ≈0.500)")
    print(f"  bump: R({t_test:.0f}d, S={DEFAULT_STABILITY_DAYS:.0f}) = {r_before:.4f} → {r_after:.4f}")
    print(f"  fresh(1d) = {r_fresh:.4f} > stale(300d) = {r_stale:.4f}")
