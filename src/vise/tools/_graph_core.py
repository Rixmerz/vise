"""Graph core tools — thin facade after 2026-06-11 split.

The original 1421-line module has been split into three focused modules:

- _graph_query.py      (~350L): graph_status, graph_check_tool,
                                graph_check_phrase, graph_get_ready_tasks
- _graph_mutation.py   (~290L): graph_reset, graph_set_node,
                                graph_acknowledge_tensions,
                                graph_record_output, graph_mid_phase_dcc,
                                graph_task_complete
- _graph_transition.py (~700L): graph_traverse (the 547-line orchestrator)
                                + _build_clean_context_briefing
                                + _target_session_matches_current

This module is kept as a facade so that:
  - tools/graph.py can continue calling register_graph_core_tools(mcp)
    without any change.
  - test_clean_context_traverse.py can continue importing
    _build_clean_context_briefing and register_graph_core_tools directly
    from vise.tools._graph_core.
"""
from __future__ import annotations

from vise.tools._graph_query import register_graph_query_tools
from vise.tools._graph_mutation import register_graph_mutation_tools
from vise.tools._graph_transition import (
    register_graph_transition_tools,
    _build_clean_context_briefing,  # re-exported for test_clean_context_traverse.py
)


def register_graph_core_tools(mcp) -> None:
    """Register all graph_* core tools on the MCP instance."""
    register_graph_query_tools(mcp)
    register_graph_mutation_tools(mcp)
    register_graph_transition_tools(mcp)


__all__ = [
    "register_graph_core_tools",
    "_build_clean_context_briefing",
]
