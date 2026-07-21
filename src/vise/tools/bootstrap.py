"""Tool registration for the vise MCP server.

Later waves register the workflow enforcer, experience memory, and
snapshot tool families here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_all(mcp: "FastMCP") -> None:
    """Register all vise tool families on *mcp*. Currently empty — filled by later waves."""
