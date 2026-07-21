"""Autonomy tier policy for recipe step execution.

Tiers define which capability effect classes a recipe is permitted to invoke:

  L1 (report-only)  — read-effect capabilities only, plus notify.* for delivery.
                       No goal_gate needed; produces reports without side effects.
  L2 (assisted)     — read and write capabilities allowed; runner HALTS before
                       any side-effecting capability and returns
                       {paused_for_approval: <step_id>} for human sign-off.
  L3 (unattended)   — all capabilities permitted; safety is delegated to the
                       existing goal_gate Stop hook (VISE_GOAL_GATE=1 + readiness
                       gate).  This module does not add a second gate; it routes
                       to the one that already exists.

Recipes without a ``tier`` field are not subject to any tier enforcement
(backward-compatible with pre-tier behaviour).
"""
from __future__ import annotations

from vise.recipes.capabilities import CAPABILITY_EFFECT

VALID_TIERS: frozenset[str] = frozenset({"L1", "L2", "L3"})

_L2_ALLOWED_EFFECTS: frozenset[str] = frozenset({"read", "write"})


def check_step(tier: str, capability: str) -> bool:
    """Return True if *capability* is allowed at *tier*, False if denied/paused.

    Args:
        tier: One of "L1", "L2", "L3".  Unknown values are treated as
              permissive (True) for forward-compatibility.
        capability: Dotted capability string, e.g. ``"web.fetch"``.

    Returns:
        True  — step may proceed.
        False — step is denied (L1) or should pause for approval (L2).

    Effect-to-tier mapping:
        L1: ``read`` caps + any ``notify.*`` cap (report delivery).
        L2: ``read`` or ``write`` caps; ``sideeffect`` caps return False.
        L3: all caps (goal_gate is the live safety net, not this function).

    Unknown capabilities default to ``sideeffect`` (most restrictive) so that
    a mis-named cap fails closed rather than open.
    """
    # Default unknown → most restrictive so misspelled caps fail closed.
    effect = CAPABILITY_EFFECT.get(capability, "sideeffect")

    if tier == "L1":
        # Allow read caps; also allow notify.* as the reporting delivery channel.
        return effect == "read" or capability.startswith("notify.")

    if tier == "L2":
        # Allow read and local-write; halt before external side effects.
        return effect in _L2_ALLOWED_EFFECTS

    if tier == "L3":
        # Unattended — all caps permitted; goal_gate Stop hook is the net.
        return True

    # Unknown tier → permissive (forward-compat).
    return True
