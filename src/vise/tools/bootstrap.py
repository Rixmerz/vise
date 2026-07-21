"""Tool registration for the vise MCP server.

Registers every tool family vise ships: snapshots, experience memory,
workflow graph (+ enforcer control), recipes/capabilities, and goals.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_all(mcp: "FastMCP") -> None:
    """Register all vise tool families on *mcp*."""
    from vise.tools.experience import register_experience
    from vise.tools.goal import register_goal
    from vise.tools.graph import register_graph
    from vise.tools.graph_enforcer_control import register_graph_enforcer_control_tools
    from vise.tools.recipes import register_recipes
    from vise.tools.snapshot import register_snapshot

    register_snapshot(mcp)
    register_experience(mcp)
    register_graph(mcp)
    register_graph_enforcer_control_tools(mcp)
    register_recipes(mcp)
    register_goal(mcp)
