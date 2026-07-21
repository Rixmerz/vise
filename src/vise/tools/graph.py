"""Unified graph tool surface.

Registers every ``graph_*`` tool directly on the MCP as plain tools
(vise has no proxy/archive layer).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

from vise.tools import _graph_builder, _graph_core, _graph_management


def register_graph(mcp: "FastMCP") -> None:
    """Register every graph_* tool on the MCP."""
    _graph_core.register_graph_core_tools(mcp)
    _graph_management.register_graph_management_tools(mcp)
    _graph_builder.register_graph_builder_tools(mcp)
