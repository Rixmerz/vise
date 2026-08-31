"""Graph transition tools: graph_traverse.

Extracted from _graph_core.py (split 2026-06-11). _graph_core is now a
thin facade that re-exports register_graph_core_tools and the shared
helpers needed by tests.

Note: _load_active_graph is intentionally duplicated in each module
(query / mutation / transition) to keep each module independently
importable without cross-module coupling.
"""
from __future__ import annotations

import subprocess
import sys

from vise.core.session import resolve_project_dir
from vise.engines.graph_engine import (
    Graph, GraphState, MaxVisitsExceeded,
    take_transition,
    _write_contract_files, _cleanup_contract_files,
    compute_ready_tasks,
)
from vise.engines.graph_parser import GraphParseError
from vise.engines.graph_state import (
    load_active_graph, save_graph_state,
)
from vise.engines.node_gate import _run_node_validators


# ---------------------------------------------------------------------------
# Internal helper (duplicated from _graph_core intentionally — isolation)
# ---------------------------------------------------------------------------

def _load_active_graph(project_dir: str) -> tuple[Graph, GraphState]:
    """Load active graph and state for a project.

    Delegates to engines.graph_state.load_active_graph — the four copies of
    this that used to live one per module drifted apart and none of them
    distinguished "never initialized" from "deliberately deactivated", so any
    read tool resurrected a workflow the user had just ended.

    Raises:
        NoActiveWorkflowError: if no graph is configured or none is active.
    """
    return load_active_graph(project_dir)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------
#
# clean_context / prior_summary / _build_clean_context_briefing /
# _target_session_matches_current removed (2026-07-31): the docstring
# claimed clean_context atomically cleared the tmux pane and slimmed the
# JSON response — none of that was true. Nothing cleared a pane,
# prompt_injection was still returned in full, and the "briefing" was
# additive, not a replacement. Its content was 100% derivable by the caller
# from fields the response already carries (new_node.id, prompt_injection,
# dag_schedule). _target_session_matches_current had zero callers.

def register_graph_transition_tools(mcp):

    @mcp.tool()
    async def graph_traverse(
        edge_id: str,
        reason: str = "Manual traverse",
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        # destructiveHint: True (modifies graph state)
        """Traverse a specific edge to move to next node.

        Use this to explicitly move through the graph. Check graph_status()
        first to see available edges.

        Args:
            edge_id: ID of the edge to traverse
            reason: Human-readable reason for this transition
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID. Remembers which project_dir this
                session last used so later calls can omit it; it does NOT
                isolate state — two sessions on one project share a graph
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

        # Find the edge
        edge = None
        for e in graph.edges:
            if e.id == edge_id:
                edge = e
                break

        if not edge:
            return {
                "error": True,
                "session_id": sid,
                "message": f"Edge '{edge_id}' not found",
                "available_edges": [e.id for e in graph.get_outgoing_edges(state.get_current_node())],
                "project_dir": resolved_dir
            }

        # Verify edge starts from current node
        current_node_id = state.get_current_node()
        if edge.from_node != current_node_id:
            return {
                "error": True,
                "session_id": sid,
                "message": f"Edge '{edge_id}' does not start from current node '{current_node_id}'",
                "edge_from": edge.from_node,
                "project_dir": resolved_dir
            }

        current_node = graph.nodes.get(current_node_id)

        # Holds the node-gate run so the validators_green check below can reuse
        # it instead of executing every validator a second time.
        node_gate: dict | None = None

        # The documented escape hatch, read once. Both gates below consult it:
        # bypassing only the node gate left a `validators_green` edge rejecting
        # the traverse anyway, which made the hatch a no-op on `feature-dev`'s
        # `spec` phase — the one place agents actually reach for it.
        import os

        gate_override = os.environ.get("VISE_NODE_GATE_OVERRIDE") == "1"

        # Node validation gate: block exit if declared validators / recipe fail
        if current_node and (
            getattr(current_node, "validators", None) or getattr(current_node, "recipe", None)
        ):
            node_gate = await _run_node_validators(current_node, resolved_dir, state)

            # Emit for EVERY check, on a green gate as much as a red one. The
            # interesting failure is a node that passed having verified nothing
            # — outcome="unverified" across the board — and restricting this to
            # the blocked branch would be blind to exactly that case.
            from vise.engines.telemetry import record_event
            for chk in (node_gate or {}).get("checks") or []:
                record_event(
                    "validator_outcome",
                    node=current_node_id,
                    validator=chk.get("name"),
                    passed=chk.get("passed"),
                    outcome=chk.get("outcome"),
                    source=chk.get("source"),
                )

            if node_gate and not node_gate["passed"]:
                # attempt tracking; env escape hatch
                st = state.node_gate_state.setdefault(current_node_id, {"attempts": 0})
                st["attempts"] += 1
                save_graph_state(resolved_dir, state)

                # `attempts` advances identically whether the gate was fixed or
                # bypassed, so it cannot answer "is anyone routing around this?".
                # These two kinds can, and they are the reason this log exists.
                #
                # A BLOCK is recorded here because it is final — the return
                # below ends the traverse. An OVERRIDE is NOT recorded here: at
                # this point it is only an intent, and a `validators_green` edge
                # further down may still reject it. Logging it now counted a
                # bypass that never happened, and one user retrying four times
                # showed up as four routed-around gates. It is recorded where it
                # actually takes effect instead.
                if not gate_override:
                    record_event(
                        "node_gate_blocked",
                        node=current_node_id,
                        attempts=st["attempts"],
                        failed=[f.get("name") for f in (node_gate.get("failed") or [])],
                    )
                    return {
                        "error": True,
                        "node_gate_blocked": True,
                        "session_id": sid,
                        "message": (
                            f"Node gate blocked: {node_gate['failed_count']} validator(s) "
                            f"failed at '{current_node_id}'. Fix and re-traverse "
                            f"(or VISE_NODE_GATE_OVERRIDE=1 to bypass)."
                        ),
                        "gate_details": node_gate,
                        "attempts": st["attempts"],
                        "project_dir": resolved_dir,
                    }

        # One record per traverse that a red gate did NOT stop. Emitted after
        # BOTH gates have had their say, so an override rejected downstream is
        # never counted as a gate someone got around — that inflated the rate
        # by one per retry.
        if gate_override and node_gate is not None and not node_gate["passed"]:
            record_event(
                "node_gate_overridden",
                node=current_node_id,
                edge=edge_id,
                failed=[f.get("name") for f in (node_gate.get("failed") or [])],
            )

        # validators_green edge: eligible only when ALL source-node validators pass.
        # Fail-closed: a source node with no validators is NOT eligible.
        if edge.condition.type == "validators_green":
            if not current_node or not (
                getattr(current_node, "validators", None) or getattr(current_node, "recipe", None)
            ):
                return {
                    "error": True,
                    "validators_green_blocked": True,
                    "session_id": sid,
                    "message": (
                        f"Edge '{edge_id}' is type 'validators_green' but source node "
                        f"'{current_node_id}' declares no validators — edge is fail-closed. "
                        f"Add validators to the source node or change the edge condition type."
                    ),
                    "project_dir": resolved_dir,
                }
            # Reuse the node-gate run above. Both checks call
            # _run_node_validators on the SAME node with the same state, so the
            # second call only duplicated cost: on feature-dev's `test` node,
            # whose only outgoing edge is validators_green, that meant running
            # the entire test suite twice on every attempt to leave the phase.
            # The `await` fallback is defensive — the two guard conditions are
            # identical, so reaching here means the gate above already ran.
            vg_result = node_gate if node_gate is not None else (
                await _run_node_validators(current_node, resolved_dir, state)
            )
            if vg_result is None or not vg_result["passed"]:
                failed_count = vg_result["failed_count"] if vg_result else 0
                # The override has to work HERE too, or it does not work at all
                # on the gate people actually hit. `feature-dev`'s `spec` node
                # exits by a validators_green edge, so bypassing only the node
                # gate above left the traverse rejected anyway — the documented
                # escape hatch ("VISE_NODE_GATE_OVERRIDE=1 to bypass", printed
                # by the node gate's own message) silently did nothing on the
                # phase where it is most often reached for.
                if not gate_override:
                    return {
                        "error": True,
                        "validators_green_blocked": True,
                        "session_id": sid,
                        "message": (
                            f"Edge '{edge_id}' (validators_green) is not eligible: "
                            f"{failed_count} validator(s) failed at '{current_node_id}'. "
                            f"Fix the failing validators and re-traverse "
                            f"(or VISE_NODE_GATE_OVERRIDE=1 to bypass)."
                        ),
                        "gate_details": vg_result,
                        "project_dir": resolved_dir,
                    }


        # Capture current HEAD SHA before transition (for 1C impact preview and entry tracking)
        entry_commit_sha: str | None = None
        try:
            sha_result = subprocess.run(
                ["git", "-C", resolved_dir, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            if sha_result.returncode == 0:
                entry_commit_sha = sha_result.stdout.strip()
        except Exception:
            pass

        # Clean up contract stubs from the current node before leaving it.
        # Stubs that have been superseded by real implementations are removed;
        # stubs still containing original content are also removed (orphans).
        _cleanup_contract_files(current_node, resolved_dir)

        # Execute transition
        try:
            state = take_transition(graph, state, edge, reason)
            # Attach commit SHA to the PathEntry just recorded
            if entry_commit_sha and state.execution_path:
                state.execution_path[-1].commit_sha = entry_commit_sha
            save_graph_state(resolved_dir, state)
        except MaxVisitsExceeded as e:
            # Get alternative edges
            other_edges = [
                ed for ed in graph.get_outgoing_edges(current_node_id)
                if ed.to_node != edge.to_node
            ]
            return {
                "error": True,
                "session_id": sid,
                "message": str(e),
                "blocked_node": e.node_id,
                "visits": e.current_visits,
                "max_visits": e.max_visits,
                "alternative_edges": [ed.id for ed in other_edges],
                "hint": "Use graph_override_max_visits() if you need to exceed the limit",
                "project_dir": resolved_dir
            }

        # Phase-transition snapshot — bypasses the 30s edit-triggered throttle.
        # Failures must NOT block traversal.
        try:
            from pathlib import Path as _Path
            from vise.core.snapshots import create_for_phase_transition as _snap_phase
            _workflow_name = graph.metadata.get("name", "unknown")
            _snap_phase(
                _Path(resolved_dir),
                workflow_name=_workflow_name,
                from_node=current_node_id,
                to_node=edge.to_node,
            )
        except Exception as _snap_exc:
            print(f"[vise.snapshot] phase-transition snapshot failed (non-fatal): {_snap_exc}", file=sys.stderr)

        # Get new node info
        new_node = graph.nodes.get(state.get_current_node())

        # Write contract files for the new node before agents start working.
        contracts_written: list[str] = []
        if new_node:
            contracts_written = _write_contract_files(new_node, resolved_dir)

        # Build prompt_injection, appending previous wave outputs if present
        base_prompt = new_node.prompt_injection if new_node else None
        prev_entry = state.execution_path[-2] if len(state.execution_path) >= 2 else None
        if prev_entry and prev_entry.outputs:
            output_lines = ["## Available from previous wave"]
            for k, v in prev_entry.outputs.items():
                output_lines.append(f"- **{k}**: {v}")
            outputs_section = "\n".join(output_lines)
            if base_prompt:
                prompt_injection = f"{base_prompt}\n\n{outputs_section}"
            else:
                prompt_injection = outputs_section
        else:
            prompt_injection = base_prompt

        # Append the experience checklist on implementation nodes. Sibling
        # injectors (pattern catalog, project metadata, node enrichers) were
        # removed: they imported modules that never shipped in this package, so
        # every one was a no-op behind a bare `except` — and the enricher's
        # `except` printed a warning about a subsystem that does not exist on
        # every single traversal. See test_no_phantom_imports.py.
        _IMPL_KEYWORDS = {"implement", "execute", "wave", "build", "code"}
        _node_id_lower = (new_node.id if new_node else "").lower()
        if any(kw in _node_id_lower for kw in _IMPL_KEYWORDS):
            try:
                from vise.engines.experience_memory import (
                    derive_implementation_checklist,
                    format_checklist_for_prompt,
                )
                _task_type = "bounded_context"
                if "feature" in _node_id_lower:
                    _task_type = "feature"
                elif "migration" in _node_id_lower:
                    _task_type = "migration"
                elif "endpoint" in _node_id_lower or "api" in _node_id_lower:
                    _task_type = "api_endpoint"
                _checklist = derive_implementation_checklist(resolved_dir, task_type=_task_type)
                if _checklist and _checklist.get("checklist"):
                    _cl_text = format_checklist_for_prompt(_checklist)
                    if _cl_text and len(_cl_text) <= 3000:
                        prompt_injection = (
                            f"{prompt_injection}\n\n{_cl_text}" if prompt_injection else _cl_text
                        )
            except Exception:
                pass

        # If new node is a DAG, compute initial ready tasks
        dag_schedule = None
        if new_node and new_node.node_type == "dag" and new_node.tasks:
            ready = compute_ready_tasks(graph, state, new_node.id)
            dag_schedule = {
                "total_tasks": len(new_node.tasks),
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
                "hint": "Launch ready tasks as parallel subagents. Call graph_task_complete(task_id) as each finishes to unlock dependent tasks.",
            }

        result = {
            "success": True,
            "session_id": sid,
            "traversed_edge": edge_id,
            "from_node": edge.from_node,
            "to_node": edge.to_node,
            "new_node": {
                "id": new_node.id if new_node else edge.to_node,
                "name": new_node.name if new_node else None,
                "mcps_enabled": new_node.mcps_enabled if new_node else [],
                "is_end": new_node.is_end if new_node else False,
                "visits": state.get_visit_count(edge.to_node)
            },
            "total_transitions": state.total_transitions,
            "prompt_injection": prompt_injection,
            "contracts_written": contracts_written,
            "dag_schedule": dag_schedule,
            "reason": reason,
            "project_dir": resolved_dir
        }

        # What the gate we just cleared actually proved. `gate_details` used to
        # appear only on the two BLOCKED returns, so a successful traversal said
        # nothing about how it succeeded — a node whose validators all skipped
        # (unconfigured tool, binary not on PATH: passed=True, source="asserted")
        # was indistinguishable from one that mechanically verified everything.
        # A skipped check is a gap to close, not a pass, and the agent can only
        # treat it that way if it is told.
        if node_gate is not None:
            result["gate_summary"] = {
                "verified": node_gate.get("verified_count", 0),
                "skipped": node_gate.get("skipped_count", 0),
                "checks": node_gate.get("checks", []),
            }
            if node_gate.get("skipped_count"):
                result["gate_summary"]["hint"] = (
                    f"{node_gate['skipped_count']} check(s) passed unverified — no "
                    f"checker actually ran (unconfigured, tool missing, nothing in "
                    f"scope, or the engine raised), and each reported "
                    f"outcome='unverified', not 'verified'. Each one is an unchecked "
                    f"defect class, not a pass. Bind it or say which risk you accept."
                )

        return result
