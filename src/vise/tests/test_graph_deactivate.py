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

import pytest
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


# ---------------------------------------------------------------------------
# Resurrection: shipped broken because nothing read state back after clearing.
# ---------------------------------------------------------------------------

def _project_with_local_graph(tmp_path) -> str:
    """A project whose .claude/workflow/graph.yaml is a minimal 2-node graph."""
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(
        "metadata:\n"
        "  name: \"Universal Debug\"\n"
        "nodes:\n"
        "  - id: \"understand\"\n"
        "    name: \"Entender\"\n"
        "    is_start: true\n"
        "    tools_blocked: [\"Write\", \"Edit\", \"Bash\"]\n"
        "  - id: \"fix\"\n"
        "    name: \"Fix\"\n"
        "    is_end: true\n"
        "edges:\n"
        "  - id: \"u-to-f\"\n"
        "    from: \"understand\"\n"
        "    to: \"fix\"\n"
    )
    return str(tmp_path)


def test_reading_state_does_not_resurrect_a_deactivated_workflow(tmp_path):
    """The bug: graph_deactivate reported success, then the next graph_status
    re-initialized the workflow — gate and all — because empty state was
    treated as "needs initializing" rather than "deliberately ended"."""
    from vise.engines.graph_state import NoActiveWorkflowError, load_active_graph

    p = _project_with_local_graph(tmp_path)
    _active_state(p)

    deactivate_graph_state(p)

    with pytest.raises(NoActiveWorkflowError):
        load_active_graph(p)

    after = load_graph_state(p)
    assert after.active_graph is None, "workflow was resurrected"
    assert after.current_nodes == []


def test_lazy_init_still_works_for_a_never_used_project(tmp_path):
    """Not-yet-initialized is a different state from deactivated, and must
    still initialize on first touch."""
    from vise.engines.graph_state import load_active_graph

    p = _project_with_local_graph(tmp_path)

    _graph, state = load_active_graph(p)
    assert state.current_nodes == ["understand"]


def test_lazy_init_resolves_the_real_library_id_not_a_slug(tmp_path):
    """Lazy-init used to slugify the DISPLAY name, inventing an id that
    matches no library graph: "Universal Debug" -> "universal-debug" while the
    real id is the yaml stem."""
    from vise.engines.graph_state import load_active_graph

    p = _project_with_local_graph(tmp_path)
    lib = tmp_path / ".claude" / "workflows"
    lib.mkdir(parents=True)
    (lib / "debug-graph.yaml").write_text(
        (tmp_path / ".claude" / "workflow" / "graph.yaml").read_text()
    )

    _graph, state = load_active_graph(p)
    assert state.active_graph == "debug-graph", (
        f"expected the library id, got {state.active_graph!r}"
    )
    assert state.active_graph_name == "Universal Debug"


def test_all_graph_modules_share_one_loader():
    """Four hand-synced copies is the arrangement that split the state tree in
    two. Each module may keep the local name, but not a local body."""
    import inspect

    from vise.engines.graph_state import load_active_graph
    from vise.tools import (
        _graph_management, _graph_mutation, _graph_query, _graph_transition,
    )

    for mod in (_graph_management, _graph_mutation, _graph_query, _graph_transition):
        src = inspect.getsource(mod._load_active_graph)
        assert "load_active_graph(project_dir)" in src, f"{mod.__name__} kept its own copy"
        assert "initialize_graph_state" not in src, f"{mod.__name__} still lazy-inits locally"
    assert callable(load_active_graph)
