"""MCP surface for the agent runtime — see docs/agent-runtime.md.

Registered tools:
    agent_list    — the agents a run can route to, and which roles are ambiguous
    run_plan      — what a DAG node would dispatch, costed, before anything runs
    run_list      — recorded runs, newest first
    run_status    — where one run stands
    run_explain   — every scheduler decision for one run, in order
    run_budget    — what a run cost, per task
    task_list     — one run's tasks and their states
    run_cancel    — ask a running scheduler to stop

**There is no ``run_start``, and that is deliberate.** This server runs *inside*
a Claude Code session; a tool that dispatched Claude Code subagents from in here
would have the session spawning sessions through the one component that is not
allowed to call another server's tools. It is the same boundary ``recipe_run``
holds — vise advises and Claude Code acts. Dispatch is the CLI's
(``vise runtime run``), where the operator is the one spending the money.

So everything here is read-only except ``run_cancel``, which only writes a
sentinel a scheduler polls.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

log = logging.getLogger(__name__)


def _root(state_dir: str | None) -> Path:
    from vise.runtime.state import runtime_root

    return Path(state_dir) if state_dir else runtime_root()


def _load(state_dir: str | None, run_id: str):
    from vise.runtime.state import RunState

    return RunState.load(_root(state_dir), run_id)


def _missing(run_id: str, state_dir: str | None) -> dict[str, Any]:
    return {
        "error": f"no run {run_id!r} under {_root(state_dir)}",
        "hint": "call run_list to see recorded runs",
    }


def register_runtime(mcp: FastMCP) -> None:
    """Register the agent-runtime tool family on *mcp*."""

    @mcp.tool()
    def agent_list() -> dict[str, Any]:
        """List the agents the runtime can route work to.

        Also names the roles that several agents share with nothing to tell them
        apart — a task with such a role and no capability in its id is
        unroutable, and this is where that shows up before a run discovers it.
        """
        from vise.runtime.registry import AgentRegistry

        registry = AgentRegistry.bundled()
        ambiguous = [
            role for role in sorted(registry.roles())
            if registry.resolve(role).agent is None
        ]
        return {
            "agents": [a.to_dict() for a in sorted(registry.agents.values(), key=lambda a: a.id)],
            "count": len(registry.agents),
            "roles": sorted(registry.roles()),
            "ambiguous_roles": ambiguous,
        }

    @mcp.tool()
    def run_plan(
        graph_path: str,
        node_id: str | None = None,
        max_cost_usd: float = 0.0,
        max_parallel: int = 4,
    ) -> dict[str, Any]:
        """Plan a DAG node's work without dispatching any of it.

        Returns the waves, the agent and model each task resolves to with the
        reasons, and an estimated total cost. ``problems`` is non-empty when the
        plan cannot run as written — an unroutable task, a dependency cycle, a
        task that does not fit the budget.
        """
        from vise.engines.graph_parser import GraphParseError, load_graph_from_file
        from vise.runtime.contracts import RunBudget
        from vise.runtime.planner import plan

        try:
            graph = load_graph_from_file(Path(graph_path))
        except (OSError, GraphParseError) as exc:
            return {"error": f"cannot read {graph_path}: {exc}"}

        candidates = [n for n in graph.nodes.values() if n.node_type == "dag" and n.tasks]
        if node_id:
            node = graph.nodes.get(node_id)
            if node is None or node.node_type != "dag" or not node.tasks:
                return {"error": f"node {node_id!r} is not a dag node with tasks"}
        elif len(candidates) == 1:
            node = candidates[0]
        elif not candidates:
            return {"error": f"{graph_path} has no dag node with tasks"}
        else:
            return {
                "error": "several dag nodes; pass node_id",
                "nodes": [n.id for n in candidates],
            }

        result = plan(
            node.tasks,
            budget=RunBudget(max_cost_usd=max_cost_usd, max_parallel=max_parallel),
        )
        payload = result.to_dict()
        payload["node"] = node.id
        payload["rendered"] = result.render()
        return payload

    @mcp.tool()
    def run_list(limit: int = 20, state_dir: str | None = None) -> dict[str, Any]:
        """List recorded runs, newest first."""
        runs_dir = _root(state_dir) / "runs"
        if not runs_dir.is_dir():
            return {"runs": [], "count": 0}
        ids = sorted(
            (p.name for p in runs_dir.iterdir() if (p / "state.json").is_file()),
            reverse=True,
        )[:limit]
        out = []
        for run_id in ids:
            state = _load(state_dir, run_id)
            if state is None:
                continue
            out.append({
                "run_id": run_id,
                "goal": state.spec.goal,
                "succeeded": state.succeeded(),
                "cancelled": state.cancelled,
                "waiting_human": bool(state.human_gate),
                "cost_usd": round(state.ledger.spent.cost_usd, 4),
                "started_at": state.started_at,
            })
        return {"runs": out, "count": len(out)}

    @mcp.tool()
    def run_status(run_id: str, state_dir: str | None = None) -> dict[str, Any]:
        """Where one run stands: every task, its state, and what it cost."""
        state = _load(state_dir, run_id)
        if state is None:
            return _missing(run_id, state_dir)
        return {
            "run_id": run_id,
            "goal": state.spec.goal,
            "succeeded": state.succeeded(),
            "done": state.is_done(),
            "cancelled": state.cancelled,
            "cancel_reason": state.cancel_reason,
            "waiting_human": state.human_gate,
            "replans": state.replans,
            "tasks": {k: v.to_dict() for k, v in sorted(state.tasks.items())},
            "budget": state.ledger.report(),
        }

    @mcp.tool()
    def task_list(run_id: str, state_dir: str | None = None) -> dict[str, Any]:
        """One run's tasks, their states, and which agent and model ran each."""
        state = _load(state_dir, run_id)
        if state is None:
            return _missing(run_id, state_dir)
        return {
            "run_id": run_id,
            "tasks": [
                {
                    "task_id": r.task_id,
                    "state": r.state.value,
                    "agent_id": r.agent_id,
                    "model": r.model,
                    "effort": r.effort,
                    "attempts": r.attempt_count,
                    "note": r.note,
                }
                for r in sorted(state.tasks.values(), key=lambda r: r.task_id)
            ],
        }

    @mcp.tool()
    def run_explain(run_id: str, limit: int = 200, state_dir: str | None = None) -> dict[str, Any]:
        """Every scheduler decision for one run, in order.

        This answers "why did this cost what it cost" and "why did that task end
        up on opus". A router whose choices cannot be read back is one nobody
        can correct.
        """
        state = _load(state_dir, run_id)
        if state is None:
            return _missing(run_id, state_dir)
        events = state.events[-limit:] if limit else state.events
        return {"run_id": run_id, "goal": state.spec.goal, "events": events,
                "truncated": len(state.events) > len(events)}

    @mcp.tool()
    def run_budget(run_id: str, state_dir: str | None = None) -> dict[str, Any]:
        """What one run cost, per task, and which ceilings were never set."""
        state = _load(state_dir, run_id)
        if state is None:
            return _missing(run_id, state_dir)
        return {"run_id": run_id, **state.ledger.report()}

    @mcp.tool()
    def run_cancel(run_id: str, state_dir: str | None = None) -> dict[str, Any]:
        """Ask a running scheduler to stop before its next dispatch.

        Writes a sentinel the scheduler polls. A run that has already finished
        will not notice, and this reports that rather than implying it worked.
        """
        from vise.runtime.state import request_cancel

        state = _load(state_dir, run_id)
        path = request_cancel(_root(state_dir), run_id)
        already_done = bool(state and state.is_done())
        return {
            "run_id": run_id,
            "sentinel": str(path),
            "already_finished": already_done,
            "note": (
                "this run had already finished; the sentinel will not do anything"
                if already_done else
                "a running scheduler stops before its next dispatch"
            ),
        }
