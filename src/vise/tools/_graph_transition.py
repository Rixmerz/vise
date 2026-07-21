"""Graph transition tools: graph_traverse.

Also contains the helpers _build_clean_context_briefing and
_target_session_matches_current which are tightly coupled to the traverse
orchestrator.

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
from vise.engines.graph_parser import load_graph_from_file, GraphParseError
from vise.engines.graph_state import (
    load_graph_state, save_graph_state, initialize_graph_state,
    get_graph_file,
)
from vise.engines.node_gate import _run_node_validators


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
# Helpers (previously module-level in _graph_core)
# ---------------------------------------------------------------------------

def _target_session_matches_current(target: str) -> bool:
    """Guard: target's session must equal caller's session.

    Without this, a stale ``pane_target`` in the global usage-state JSON
    (written by a different project) can cause inject_after_clear to send
    /clear into the wrong pane. When caller is not inside a terminal
    multiplexer, we cannot validate — allow through (fall back to
    single-pane semantics).

    Uses direct tmux subprocess calls; returns False if tmux is unavailable.
    """
    import os as _os

    # tmux path: use TMUX_PANE env var as the anchor.
    caller_pane = _os.environ.get("TMUX_PANE")
    if not caller_pane:
        return True

    import subprocess as _sp
    try:
        caller = _sp.run(
            ["tmux", "display-message", "-p", "-t", caller_pane, "#S"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        tgt = _sp.run(
            ["tmux", "display-message", "-p", "-t", target, "#S"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (FileNotFoundError, _sp.CalledProcessError):
        return False
    return bool(caller and tgt and caller == tgt)


def _build_clean_context_briefing(
    prompt_injection: str | None,
    prior_summary: str | None,
    dag_schedule: dict | None,
    new_node_id: str,
) -> str:
    """Build a compact briefing string for clean_context injection.

    Sections (in order): prior summary, next node, prompt injection,
    DAG schedule, next action. Total target: under 6000 chars.
    """
    MAX_CHARS = 6000
    sections: list[str] = []

    if prior_summary:
        sections.append(f"## Resume — prior wave summary\n{prior_summary.strip()}")

    sections.append(f"## Next node: {new_node_id}")

    if prompt_injection:
        sections.append(prompt_injection.strip())

    if dag_schedule and isinstance(dag_schedule, dict):
        ready = dag_schedule.get("ready_tasks", [])
        lines = [
            "## DAG schedule",
            f"- Ready tasks: {len(ready)} — {', '.join(t['id'] for t in ready[:10])}",
            dag_schedule.get("hint", ""),
        ]
        sections.append("\n".join(line for line in lines if line))

    sections.append("## Next action\nBegin work on this node. Call `graph_status` to verify current phase, then execute the node's instructions above.")

    briefing = "\n\n".join(sections)
    if len(briefing) > MAX_CHARS:
        briefing = briefing[:MAX_CHARS - 3] + "..."
    return briefing


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_graph_transition_tools(mcp):

    @mcp.tool()
    async def graph_traverse(
        edge_id: str,
        reason: str = "Manual traverse",
        project_dir: str | None = None,
        session_id: str | None = None,
        clean_context: bool = True,
        prior_summary: str | None = None,
    ) -> dict:
        # destructiveHint: True (modifies graph state)
        """Traverse a specific edge to move to next node.

        Use this to explicitly move through the graph. Check graph_status()
        first to see available edges.

        Args:
            edge_id: ID of the edge to traverse
            reason: Human-readable reason for this transition
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
            clean_context: When True (default), atomically clears the tmux pane and
                pastes the full briefing as the new turn's prompt. The JSON response
                is slimmed (no prompt_injection). When False, returns the full payload
                unchanged (legacy behavior for non-tmux / CI environments).
            prior_summary: Optional summary of the previous wave's work to prepend
                to the briefing when clean_context=True. Has no effect when False.
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

        # Node validation gate: block exit if declared validators / recipe fail
        if current_node and (
            getattr(current_node, "validators", None) or getattr(current_node, "recipe", None)
        ):
            node_gate = await _run_node_validators(current_node, resolved_dir, state)
            if node_gate and not node_gate["passed"]:
                # attempt tracking; env escape hatch
                st = state.node_gate_state.setdefault(current_node_id, {"attempts": 0})
                st["attempts"] += 1
                save_graph_state(resolved_dir, state)
                import os
                if os.environ.get("VISE_NODE_GATE_OVERRIDE") != "1":
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
            vg_result = await _run_node_validators(current_node, resolved_dir, state)
            if vg_result is None or not vg_result["passed"]:
                failed_count = vg_result["failed_count"] if vg_result else 0
                return {
                    "error": True,
                    "validators_green_blocked": True,
                    "session_id": sid,
                    "message": (
                        f"Edge '{edge_id}' (validators_green) is not eligible: "
                        f"{failed_count} validator(s) failed at '{current_node_id}'. "
                        f"Fix the failing validators and re-traverse."
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

        # Record trend snapshot on transition
        try:
            from vise.engines.graph_state import _get_centralized_state_dir
            from vise.engines.trend_tracker import record_snapshot
            _trend_state_dir = str(_get_centralized_state_dir(resolved_dir))
            record_snapshot(resolved_dir, _trend_state_dir, {})
        except Exception:
            pass

        # Previous node's entry commit SHA (used by enrichers for accurate diffs)
        prev_entry_sha: str | None = None
        if len(state.execution_path) >= 2:
            prev_entry = state.execution_path[-2]
            prev_entry_sha = prev_entry.commit_sha if hasattr(prev_entry, 'commit_sha') else None

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

        # Conditionally inject patterns, checklist, metadata for implementation nodes
        _IMPL_KEYWORDS = {"implement", "execute", "wave", "build", "code"}
        _node_id_lower = (new_node.id if new_node else "").lower()
        if any(kw in _node_id_lower for kw in _IMPL_KEYWORDS):
            _injections: list[str] = []
            _budget = 6000

            try:
                from vise.engines.graph_state import _get_centralized_state_dir
                _state_dir = str(_get_centralized_state_dir(resolved_dir))
            except Exception:
                _state_dir = ""

            if _state_dir:
                # Pattern catalog
                try:
                    from vise.engines.pattern_catalog import PatternCatalog
                    _pc = PatternCatalog.load(resolved_dir, _state_dir)
                    if _pc:
                        _snippet = _pc.to_prompt_injection()
                        if _snippet and len(_snippet) <= 2500:
                            _injections.append(_snippet)
                            _budget -= len(_snippet)
                except Exception:
                    pass

                # Experience checklist
                try:
                    from vise.engines.experience_memory import derive_implementation_checklist, format_checklist_for_prompt
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
                        if _cl_text and len(_cl_text) <= min(3000, _budget):
                            _injections.append(_cl_text)
                            _budget -= len(_cl_text)
                except Exception:
                    pass

                # Project metadata (key sections only)
                try:
                    from vise.engines.project_metadata import ProjectMetadata
                    _pm = ProjectMetadata.load(resolved_dir, _state_dir)
                    if _pm:
                        _meta = _pm.get()
                        _sections = []
                        for _key in ("migration_number", "id_patterns", "bounded_contexts"):
                            if _key in _meta and _meta[_key]:
                                import json as _json
                                _sections.append(f"- **{_key}**: `{_json.dumps(_meta[_key], default=str)[:500]}`")
                        if _sections:
                            _meta_text = "## Project Metadata\n" + "\n".join(_sections)
                            if len(_meta_text) <= _budget:
                                _injections.append(_meta_text)
                                _budget -= len(_meta_text)
                except Exception:
                    pass

            if _injections:
                _extra = "\n\n".join(_injections)
                prompt_injection = f"{prompt_injection}\n\n{_extra}" if prompt_injection else _extra

        # NodeEnricher: run all active enrichers ADDITIVELY after the existing briefing
        # is fully assembled.  Fail-soft: any exception → traverse is unaffected.
        # Parity: when no enrichers are active, prompt_injection is byte-identical to before.
        try:
            from vise.contracts.enricher import NodeContext as _NodeContext
            from vise.engines.enricher_runner import run_enrichers as _run_enrichers

            # Derive changed_files via git diff against the previous node's commit SHA.
            # Reuse prev_entry_sha already computed above; falls back to HEAD~1.
            _enricher_changed: list[str] = []
            try:
                _diff_base = prev_entry_sha or "HEAD~1"
                _diff_result = subprocess.run(
                    ["git", "-C", resolved_dir, "diff", "--name-only", _diff_base, "HEAD"],
                    capture_output=True, text=True, timeout=5
                )
                if _diff_result.returncode == 0:
                    _enricher_changed = [
                        str(p) for raw in _diff_result.stdout.splitlines()
                        if (p := __import__("pathlib").Path(resolved_dir) / raw.strip()) and p.is_file()
                    ]
            except Exception:
                pass

            _enrich_ctx = _NodeContext(
                project_dir=resolved_dir,
                node_id=new_node.id if new_node else edge.to_node,
                node_name=new_node.name if new_node else edge.to_node,
                phase="traverse",
                changed_files=_enricher_changed,
                token_budget=900,
                prev_commit_sha=prev_entry_sha,
                baseline=getattr(state, "baseline_smells", None),
            )
            _enrich_out = await _run_enrichers(_enrich_ctx)
            _combined = _enrich_out.get("combined_prompt", "")
            if _combined:
                prompt_injection = (
                    f"{prompt_injection}\n\n{_combined}" if prompt_injection else _combined
                )
        except Exception as _enrich_exc:
            # Fail-soft: enricher failure must never block or alter a traversal.
            print(
                f"[vise] Warning: enricher wiring failed (non-fatal): {_enrich_exc}",
                file=sys.stderr,
            )

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

        # clean_context: terminal injection removed with session-operator cluster.
        # The briefing is built but not injected — returned in the response for
        # the caller to use via next_task_record + SessionStart hooks.
        if clean_context:
            try:
                briefing = _build_clean_context_briefing(
                    prompt_injection=prompt_injection,
                    prior_summary=prior_summary,
                    dag_schedule=dag_schedule,
                    new_node_id=new_node.id if new_node else edge.to_node,
                )
                if briefing.strip():
                    result["briefing"] = briefing
                    result["briefing_chars"] = len(briefing)
            except Exception as e:
                import sys as _sys
                print(f"[vise] clean_context briefing failed (non-fatal): {e}", file=_sys.stderr)

        return result
