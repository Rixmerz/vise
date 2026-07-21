"""Graph mutation tools: graph_reset, graph_set_node, graph_acknowledge_tensions,
graph_record_output, graph_mid_phase_dcc, graph_task_complete.

Extracted from _graph_core.py (split 2026-06-11). _graph_core is now a
thin facade that re-exports register_graph_core_tools and the shared
helpers needed by tests.

Note: _load_active_graph is intentionally duplicated in each module
(query / mutation / transition) to keep each module independently
importable without cross-module coupling.
"""
from __future__ import annotations

import sys

from vise.core.session import resolve_project_dir
from vise.engines.config import load_enforcer_config
from vise.engines.graph_engine import (
    Graph, GraphState,
    compute_ready_tasks, is_dag_complete,
)
from vise.engines.graph_parser import load_graph_from_file, GraphParseError
from vise.engines.graph_state import (
    load_graph_state, save_graph_state, initialize_graph_state,
    reset_graph_state, get_graph_file,
)
from vise.engines.dcc_glue import (
    _is_dcc_available,
    _clear_tension_gate_state,
    _execute_dcc_tool,
    acknowledge_tension_gate,
    _run_mid_phase_check,
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

def register_graph_mutation_tools(mcp):

    @mcp.tool()
    def graph_task_complete(
        task_id: str,
        outputs: dict[str, str] | None = None,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Mark a DAG task as complete and return newly unblocked tasks.

        Call this when a subagent finishes its assigned task. The engine will
        compute which tasks are now unblocked and return them.

        Args:
            task_id: ID of the completed task
            outputs: Optional key-value outputs (forwarded to dependent tasks)
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

        task_ids = {t.id for t in current_node.tasks}
        if task_id not in task_ids:
            return {
                "error": True,
                "session_id": sid,
                "message": f"Task '{task_id}' not found in node '{node_id}'",
                "available_tasks": list(task_ids),
                "project_dir": resolved_dir,
            }

        if state.is_task_complete(node_id, task_id):
            return {
                "error": True,
                "session_id": sid,
                "message": f"Task '{task_id}' is already complete",
                "project_dir": resolved_dir,
            }

        # Capture ready-set BEFORE marking complete so ``newly_ready``
        # can be a true delta (tasks whose deps were blocked on the
        # just-completed one) instead of the whole frontier.
        before_ready = {t.id for t in compute_ready_tasks(graph, state, node_id)}
        state.mark_task_complete(node_id, task_id, outputs)
        save_graph_state(resolved_dir, state)

        after_ready = compute_ready_tasks(graph, state, node_id)
        is_complete = is_dag_complete(graph, state, node_id)
        completed_count = len(state.get_completed_tasks_for_node(node_id))

        def _task_view(t):
            return {
                "id": t.id,
                "name": t.name,
                "prompt": t.prompt,
                "dependencies": t.dependencies,
                "tools_blocked": t.tools_blocked,
                "mcps_enabled": t.mcps_enabled,
            }

        newly_unblocked = [t for t in after_ready if t.id not in before_ready]
        still_ready = [t for t in after_ready if t.id in before_ready]

        return {
            "success": True,
            "session_id": sid,
            "completed": task_id,
            "newly_ready": [_task_view(t) for t in newly_unblocked],
            "still_ready": [_task_view(t) for t in still_ready],
            "ready": [_task_view(t) for t in after_ready],
            "is_dag_complete": is_complete,
            "completed_count": completed_count,
            "total_tasks": len(current_node.tasks),
            "remaining": len(current_node.tasks) - completed_count,
            "project_dir": resolved_dir,
        }

    @mcp.tool()
    def graph_reset(project_dir: str | None = None, session_id: str | None = None) -> dict:
        # destructiveHint: True (clears graph state)
        """Reset graph to start node.

        Clears all visit counts and execution history.

        Args:
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, _ = _load_active_graph(resolved_dir)
        except (ValueError, GraphParseError) as e:
            return {
                "error": True,
                "session_id": sid,
                "message": str(e),
                "project_dir": resolved_dir
            }

        state = reset_graph_state(resolved_dir, graph)
        _clear_tension_gate_state(state)
        start_node = graph.get_start_node()

        return {
            "success": True,
            "session_id": sid,
            "message": "Graph reset to start node",
            "current_node": {
                "id": start_node.id if start_node else None,
                "name": start_node.name if start_node else None
            },
            "project_dir": resolved_dir
        }

    @mcp.tool()
    def graph_set_node(
        node_id: str,
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # destructiveHint: True (bypasses normal transition logic)
        """Jump to a specific node (admin function).

        Use with caution - bypasses normal transition logic.

        Args:
            node_id: ID of the node to jump to
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, state = _load_active_graph(resolved_dir)
        except (ValueError, GraphParseError) as e:
            return {
                "error": True,
                "session_id": sid,
                "message": str(e),
                "project_dir": resolved_dir
            }

        if node_id not in graph.nodes:
            return {
                "error": True,
                "session_id": sid,
                "message": f"Node '{node_id}' not found",
                "available_nodes": list(graph.nodes.keys()),
                "project_dir": resolved_dir
            }

        # Record the jump
        state.record_transition(
            from_node=state.get_current_node(),
            to_node=node_id,
            edge_id=None,
            reason=f"Admin jump to {node_id}"
        )
        save_graph_state(resolved_dir, state)
        _clear_tension_gate_state(state, node_id)

        node = graph.nodes[node_id]
        return {
            "success": True,
            "session_id": sid,
            "message": f"Jumped to node '{node_id}'",
            "current_node": {
                "id": node.id,
                "name": node.name,
                "mcps_enabled": node.mcps_enabled,
                "is_end": node.is_end,
                "visits": state.get_visit_count(node_id)
            },
            "prompt_injection": node.prompt_injection,
            "project_dir": resolved_dir
        }

    @mcp.tool()
    async def graph_acknowledge_tensions(
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # destructiveHint: True (bypasses tension gate)
        """Acknowledge unresolved tensions and force-advance past the tension gate.

        Use this as an escape hatch when the agent has reviewed the tensions but
        decides to proceed anyway. The next graph_traverse() from this node will
        skip the tension gate check.

        Args:
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        try:
            graph, state = _load_active_graph(resolved_dir)
        except (ValueError, GraphParseError) as e:
            return {
                "error": True,
                "session_id": sid,
                "message": str(e),
                "project_dir": resolved_dir
            }

        current_node_id = state.get_current_node()
        current_node = graph.nodes.get(current_node_id) if current_node_id else None

        if not current_node or not current_node.dcc_context:
            return {
                "error": True,
                "session_id": sid,
                "message": f"Node '{current_node_id}' has no tension gate configured",
                "project_dir": resolved_dir
            }

        gate_config = current_node.dcc_context.get("tension_gate", {})
        if not gate_config.get("enabled", False):
            return {
                "error": True,
                "session_id": sid,
                "message": f"Node '{current_node_id}' has no tension gate enabled",
                "project_dir": resolved_dir
            }

        gate_state = acknowledge_tension_gate(resolved_dir, current_node_id, state)
        save_graph_state(resolved_dir, state)

        # Mark tensions as reviewed in DCC
        try:
            await _execute_dcc_tool("cube_get_tensions", {"status": "reviewed"}, resolved_dir)
        except Exception as e:
            print(f"[vise] Warning: failed to mark tensions as reviewed: {e}", file=sys.stderr)
            pass

        return {
            "success": True,
            "session_id": sid,
            "message": f"Tensions acknowledged for node '{current_node_id}'. Next traverse will pass the gate.",
            "node_id": current_node_id,
            "attempts_before_ack": gate_state["attempts"],
            "project_dir": resolved_dir
        }

    @mcp.tool()
    async def graph_mid_phase_dcc(
        files: list[str],
        project_dir: str | None = None,
    ) -> dict:
        """[CATEGORY: analysis] Run lightweight DCC analysis on specific files between workflow traversals.

        Use this tool when you want DCC feedback on files you've modified without
        advancing the workflow. Provides continuous quality feedback similar to
        IDE-integrated analysis.

        Args:
            files: List of file paths that were recently modified
            project_dir: Project directory (uses session default if not specified)
        """
        resolved_dir, _sid = resolve_project_dir(project_dir)

        if not _is_dcc_available():
            return {"error": "DCC (deltacodecube) MCP not configured"}

        enforcer_config = load_enforcer_config(resolved_dir)

        if not enforcer_config.get("mid_phase_dcc", False):
            return {"error": "mid_phase_dcc not enabled in config"}

        if not enforcer_config.get("dcc_injection_enabled", True):
            return {"error": "dcc_injection_enabled is False in config"}

        baseline_smells = None
        try:
            _, _mid_state = _load_active_graph(resolved_dir)
            baseline_smells = _mid_state.baseline_smells
        except Exception:
            pass

        result = await _run_mid_phase_check(resolved_dir, files, baseline_smells=baseline_smells)
        if result is None:
            return {"error": "DCC mid-phase check returned no result"}

        return result

    @mcp.tool()
    def graph_record_output(
        key: str,
        value: str,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Record an output from the current workflow node.

        Agents call this to register what they produced during a phase.
        These outputs are injected into the next node's prompt when traversing.

        Example: After discovering the next migration number, call:
            graph_record_output(key="next_migration", value="000028")

        The next wave's agents will receive:
            "## Available from previous wave: next_migration = 000028"

        Args:
            key: Short identifier for the output (e.g., "migration_number", "types_file")
            value: The value to record (string)
            project_dir: Optional project directory
            session_id: Optional session ID
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

        if not state.execution_path:
            return {
                "error": True,
                "session_id": sid,
                "message": "No active path entry to record output on",
                "project_dir": resolved_dir,
            }

        last_entry = state.execution_path[-1]
        if last_entry.outputs is None:
            last_entry.outputs = {}
        last_entry.outputs[key] = value

        save_graph_state(resolved_dir, state)

        return {
            "success": True,
            "session_id": sid,
            "key": key,
            "value": value,
            "current_outputs": last_entry.outputs,
            "node": state.get_current_node(),
            "project_dir": resolved_dir,
        }
