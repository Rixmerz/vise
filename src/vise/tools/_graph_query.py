"""Graph query tools: graph_status, graph_check_tool, graph_check_phrase,
graph_get_ready_tasks.

Extracted from _graph_core.py (split 2026-06-11). _graph_core is now a
thin facade that re-exports register_graph_core_tools and the shared
helpers needed by tests.

Note: _load_active_graph is intentionally duplicated in each module
(query / mutation / transition) to keep each module independently
importable without cross-module coupling.
"""
from __future__ import annotations

from vise.core.session import resolve_project_dir
from vise.engines.config import load_enforcer_config
from vise.engines.graph_engine import (
    Graph, GraphState,
    evaluate_transitions,
    compute_ready_tasks, is_dag_complete,
)
from vise.engines.graph_parser import load_graph_from_file, GraphParseError
from vise.engines.graph_state import (
    load_graph_state, initialize_graph_state,
    get_graph_file, get_node_visit_warning,
)


# ---------------------------------------------------------------------------
# Internal helper (duplicated from _graph_core intentionally — isolation)
# ---------------------------------------------------------------------------

def _load_active_graph(project_dir: str) -> tuple[Graph, GraphState]:
    """Load active graph and state for a project.

    Returns:
        Tuple of (Graph, GraphState)

    Raises:
        ValueError: If no graph is configured
    """
    graph_file = get_graph_file(project_dir)
    if not graph_file.exists():
        raise ValueError(f"No graph.yaml found at {graph_file}")

    graph = load_graph_from_file(graph_file)
    state = load_graph_state(project_dir)

    # Initialize state if empty
    if not state.current_nodes:
        graph_name = graph.metadata.get('name', 'unnamed')
        state = initialize_graph_state(project_dir, graph, graph_name)

    return graph, state


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_graph_query_tools(mcp):

    @mcp.tool()
    def graph_status(project_dir: str | None = None, session_id: str | None = None) -> dict:
        # readOnlyHint: True
        """Get current graph workflow status: current node, available edges, visits.

        Returns the current node, outgoing edges sorted by priority,
        and visit counts for loop protection monitoring.

        Args:
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, state = _load_active_graph(resolved_dir)
        except ValueError as e:
            return {
                "error": True,
                "session_id": sid,
                "message": str(e),
                "hint": "Create a graph.yaml file or use graph_activate() to load one",
                "project_dir": resolved_dir
            }
        except GraphParseError as e:
            return {
                "error": True,
                "session_id": sid,
                "message": f"Graph parse error: {e}",
                "project_dir": resolved_dir
            }

        current_node_id = state.get_current_node()
        current_node = graph.nodes.get(current_node_id) if current_node_id else None

        # Get outgoing edges
        outgoing_edges = graph.get_outgoing_edges(current_node_id) if current_node_id else []
        edges_info = []
        for edge in outgoing_edges:
            edge_info = {
                "id": edge.id,
                "to": edge.to_node,
                "to_name": graph.nodes[edge.to_node].name if edge.to_node in graph.nodes else edge.to_node,
                "condition_type": edge.condition.type,
                "priority": edge.priority
            }
            if edge.condition.tool:
                edge_info["condition_tool"] = edge.condition.tool
            if edge.condition.phrases:
                edge_info["condition_phrases"] = edge.condition.phrases
            edges_info.append(edge_info)

        # Check for visit warnings
        warnings = []
        if current_node:
            warning = get_node_visit_warning(state, current_node_id, current_node.max_visits)
            if warning:
                warnings.append(warning)

        # Get enforcer config
        enforcer_config = load_enforcer_config(resolved_dir)

        # Collect outputs recorded on the current node's path entry
        current_outputs: dict[str, str] = {}
        if state.execution_path:
            last_entry = state.execution_path[-1]
            if last_entry.outputs:
                current_outputs = last_entry.outputs

        # DAG info (only when current node is a DAG)
        dag_info = None
        if current_node and current_node.node_type == "dag" and current_node.tasks:
            completed_ids = set(state.get_completed_tasks_for_node(current_node.id))
            ready = compute_ready_tasks(graph, state, current_node.id)
            ready_ids = {t.id for t in ready}
            blocked_ids = {t.id for t in current_node.tasks if t.id not in completed_ids and t.id not in ready_ids}
            dag_info = {
                "total_tasks": len(current_node.tasks),
                "completed": list(completed_ids),
                "ready": [
                    {
                        "id": t.id,
                        "name": t.name,
                        "prompt": t.prompt[:200] if t.prompt else None,
                        "tools_blocked": t.tools_blocked,
                        "mcps_enabled": t.mcps_enabled,
                    }
                    for t in ready
                ],
                "blocked": list(blocked_ids),
                "is_complete": is_dag_complete(graph, state, current_node.id),
            }

        return {
            "session_id": sid,
            "graph_name": state.active_graph or graph.metadata.get('name', 'unnamed'),
            "current_node": {
                "id": current_node_id,
                "name": current_node.name if current_node else None,
                "mcps_enabled": current_node.mcps_enabled if current_node else [],
                "tools_blocked": current_node.tools_blocked if current_node else [],
                "is_end": current_node.is_end if current_node else False,
                "visits": state.get_visit_count(current_node_id) if current_node_id else 0,
                "max_visits": current_node.max_visits if current_node else 10
            },
            "available_edges": edges_info,
            "total_transitions": state.total_transitions,
            "warnings": warnings if warnings else None,
            "enabled": enforcer_config.get("enforcer_enabled", True),
            "prompt_injection": current_node.prompt_injection if current_node else None,
            "current_outputs": current_outputs,
            "dag_info": dag_info,
            "last_activity": state.last_activity,
            "project_dir": resolved_dir
        }

    @mcp.tool()
    def graph_get_ready_tasks(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Return tasks in the current DAG node that can run now.

        Use this to check which tasks have their dependencies satisfied
        and can be launched as parallel subagents.

        Args:
            project_dir: Project directory
            session_id: Session ID
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, state = _load_active_graph(resolved_dir)
        except (ValueError, GraphParseError) as e:
            return {
                "error": True,
                "session_id": sid,
                "message": str(e),
                "project_dir": resolved_dir,
            }

        node_id = state.get_current_node()
        current_node = graph.nodes.get(node_id) if node_id else None

        if not current_node or current_node.node_type != "dag":
            return {
                "error": True,
                "session_id": sid,
                "message": f"Current node '{node_id}' is not a DAG node",
                "project_dir": resolved_dir,
            }

        ready = compute_ready_tasks(graph, state, node_id)
        completed_ids = set(state.get_completed_tasks_for_node(node_id))

        return {
            "session_id": sid,
            "node_id": node_id,
            "ready_tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "prompt": t.prompt,
                    "dependencies": t.dependencies,
                    "tools_blocked": t.tools_blocked,
                    "mcps_enabled": t.mcps_enabled,
                }
                for t in ready
            ],
            "completed_count": len(completed_ids),
            "total_tasks": len(current_node.tasks),
            "is_dag_complete": is_dag_complete(graph, state, node_id),
            "project_dir": resolved_dir,
        }

    @mcp.tool()
    def graph_check_tool(
        mcp_name: str,
        tool_name: str,
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # readOnlyHint: True
        """Check if a tool call would trigger any edge transitions.

        Use this BEFORE executing a tool to see if it would cause a transition.
        Does NOT execute the transition - use graph_traverse() for that.

        Args:
            mcp_name: Name of the MCP server
            tool_name: Name of the tool
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, state = _load_active_graph(resolved_dir)
        except (ValueError, GraphParseError) as e:
            return {
                "matched": False,
                "session_id": sid,
                "message": str(e),
                "project_dir": resolved_dir
            }

        # Evaluate transitions
        trigger_value = {'mcp': mcp_name, 'tool': tool_name}
        matching_edges = evaluate_transitions(graph, state, 'tool', trigger_value)

        if not matching_edges:
            return {
                "matched": False,
                "session_id": sid,
                "message": f"Tool '{mcp_name}.{tool_name}' does not trigger any transitions",
                "current_node": state.get_current_node(),
                "project_dir": resolved_dir
            }

        edges_info = []
        for edge in matching_edges:
            edges_info.append({
                "id": edge.id,
                "to": edge.to_node,
                "to_name": graph.nodes[edge.to_node].name if edge.to_node in graph.nodes else edge.to_node,
                "priority": edge.priority
            })

        return {
            "matched": True,
            "session_id": sid,
            "tool": f"{mcp_name}.{tool_name}",
            "matching_edges": edges_info,
            "recommended_edge": matching_edges[0].id if matching_edges else None,
            "hint": "Use graph_traverse(edge_id) to execute the transition",
            "project_dir": resolved_dir
        }

    @mcp.tool()
    def graph_check_phrase(
        text: str,
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # readOnlyHint: True
        """Check if text contains phrases that would trigger edge transitions.

        Use this to indicate conditions through phrases (e.g., "trivial", "no docs needed").
        Does NOT execute the transition - use graph_traverse() for that.

        Args:
            text: Text to check against edge phrases
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, state = _load_active_graph(resolved_dir)
        except (ValueError, GraphParseError) as e:
            return {
                "matched": False,
                "session_id": sid,
                "message": str(e),
                "project_dir": resolved_dir
            }

        # Evaluate transitions
        trigger_value = {'text': text}
        matching_edges = evaluate_transitions(graph, state, 'phrase', trigger_value)

        if not matching_edges:
            # Get available phrases from current node's edges
            current_edges = graph.get_outgoing_edges(state.get_current_node())
            all_phrases = []
            for edge in current_edges:
                if edge.condition.phrases:
                    all_phrases.extend(edge.condition.phrases)

            return {
                "matched": False,
                "session_id": sid,
                "message": "No matching phrases found",
                "current_node": state.get_current_node(),
                "available_phrases": all_phrases if all_phrases else None,
                "project_dir": resolved_dir
            }

        # Find which phrase matched
        matched_phrase = None
        for edge in matching_edges:
            _, phrase = edge.condition.matches_phrase(text)
            if phrase:
                matched_phrase = phrase
                break

        edges_info = []
        for edge in matching_edges:
            edges_info.append({
                "id": edge.id,
                "to": edge.to_node,
                "to_name": graph.nodes[edge.to_node].name if edge.to_node in graph.nodes else edge.to_node,
                "priority": edge.priority
            })

        return {
            "matched": True,
            "session_id": sid,
            "matched_phrase": matched_phrase,
            "matching_edges": edges_info,
            "recommended_edge": matching_edges[0].id if matching_edges else None,
            "hint": "Use graph_traverse(edge_id) to execute the transition",
            "project_dir": resolved_dir
        }
