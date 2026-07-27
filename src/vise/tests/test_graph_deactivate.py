"""Regression tests for ending an active workflow.

A workflow could be started but never finished. The first time the phase gate
actually fired (it had never fired before — see hooks/_xdg.py), a workflow left
active by an earlier session blocked unrelated work, and there was no honest
way out:

  - `graph_reset` returns to the START node, which is the most restrictive node
    in every bundled workflow, so it re-arms the gate rather than releasing it.
  - `graph_enforcer_toggle(False)` explicitly does not clear state, so it mutes
    gating for every FUTURE workflow too.
  - the bundled debug workflow has no node at all from which the trap could be
    fixed: even its terminal `report` node blocks Edit.

These lock in the exit and the property that made it necessary.
"""

import json

from vise.engines.graph_state import (
    deactivate_graph_state,
    load_graph_state,
    save_graph_state,
)
from vise.engines.graph_engine import GraphState
from vise.hooks.graph_enforcer import GRAPH_INNER_ALLOWLIST


def _active_state(project_dir: str, node: str = "understand") -> None:
    save_graph_state(
        project_dir,
        GraphState(
            current_nodes=[node],
            node_visits={node: 1},
            execution_path=[],
            active_graph="debug-graph",
            active_graph_name="Universal Debug",
        ),
    )


def test_deactivate_clears_the_active_workflow(tmp_path):
    p = str(tmp_path)
    _active_state(p)

    result = deactivate_graph_state(p)

    assert result["deactivated"] is True
    assert result["was_active"] == "debug-graph"
    assert result["was_active_name"] == "Universal Debug"

    after = load_graph_state(p)
    assert after.active_graph is None
    assert after.current_nodes == [], "a cleared workflow must gate nothing"


def test_deactivate_preserves_execution_history(tmp_path):
    """Ending a workflow records that it ran; it does not erase it."""
    p = str(tmp_path)
    _active_state(p)

    deactivate_graph_state(p)

    after = load_graph_state(p)
    assert after.execution_path, "history was erased"
    assert "deactivated" in after.execution_path[-1].reason


def test_deactivate_is_idempotent(tmp_path):
    p = str(tmp_path)
    _active_state(p)

    assert deactivate_graph_state(p)["deactivated"] is True
    second = deactivate_graph_state(p)
    assert second["deactivated"] is False
    assert second["reason"] == "no active workflow"


def test_enforcer_approves_everything_once_deactivated(tmp_path, monkeypatch):
    """The point of the exit: the gate stops gating.

    Drives the hook the same way Claude Code does — a tool call on stdin — so
    this asserts the real decision, not an internal helper's return value.
    """
    import io
    import sys

    from vise.hooks import graph_enforcer

    p = str(tmp_path)
    _active_state(p)  # `understand` blocks Write in the bundled debug workflow
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", p)

    def _decide() -> str:
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps({"tool_name": "Write", "tool_input": {}}))
        )
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        graph_enforcer.main()
        return json.loads(out.getvalue() or '{"decision": "approve"}')["decision"]

    deactivate_graph_state(p)
    assert _decide() == "approve", "a deactivated workflow must not gate anything"


def test_the_exits_are_unblockable():
    """Guard for the dead-recovery-hatch class of bug.

    An allowlist that does not contain the exit is how a misconfigured workflow
    locks the user out with no in-band escape. `graph_reset` alone is not
    enough — it is not an exit.
    """
    for tool in ("graph_deactivate", "graph_set_node", "graph_status", "graph_enforcer_toggle"):
        assert tool in GRAPH_INNER_ALLOWLIST, f"{tool} must never be blockable"
