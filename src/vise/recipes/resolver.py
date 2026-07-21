"""Capability resolver — maps a capability string to (mcp_name, tool_name).

Resolution order (MVP):
  1. User pin in <project>/.vise/recipe-defaults.yaml
  2. First registered match in capabilities.yaml assignments
  3. Vise-internal bindings (INTERNAL_BINDINGS)
  4. No match -> None
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from vise.recipes.capabilities import INTERNAL_BINDINGS

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def resolve_capability(
    capability: str,
    assignments: dict[str, str],
    user_pins: dict[str, str],
) -> tuple[str, str] | None:
    """Resolve a capability to (mcp_name, tool_name).

    Args:
        capability: e.g. "web.scrape"
        assignments: tool -> capability mapping from capabilities.yaml
        user_pins: capability -> "mcp.tool" mapping from recipe-defaults.yaml

    Returns:
        (mcp_name, tool_name) tuple or None if unresolved.
    """
    # 1. User pin overrides everything
    if capability in user_pins:
        pinned = user_pins[capability]
        parts = pinned.split(".", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
        log.warning("[recipes] user pin for %r has invalid format %r", capability, pinned)

    # 2. First assignment match (deterministic: alphabetical key order preserved by dict)
    for tool, cap in assignments.items():
        if cap == capability:
            parts = tool.split(".", 1)
            if len(parts) == 2:
                return (parts[0], parts[1])
            log.warning("[recipes] assignment tool %r has invalid format (expected mcp.tool)", tool)

    # 3. Vise-internal bindings
    if capability in INTERNAL_BINDINGS:
        return INTERNAL_BINDINGS[capability]

    return None


def audit_unresolved(
    assignments: dict[str, str],
    user_pins: dict[str, str],
    all_capabilities: list[str],
) -> list[str]:
    """Return the list of capabilities that cannot be resolved."""
    unresolved: list[str] = []
    for cap in all_capabilities:
        resolved = resolve_capability(cap, assignments, user_pins)
        if resolved is None:
            unresolved.append(cap)
    return unresolved
