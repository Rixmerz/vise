"""What each MCP tool does to the world, in the four hints the protocol has.

The host renders a tool's name, its title and its raw arguments when it asks a
person to approve a call, and nothing else. Without annotations every one of
vise's tools looked alike in that prompt: ``graph_status``, which reads a JSON
file, and ``snapshot_restore``, which overwrites the working tree, arrived with
the same weight. Some clients also refuse to run an unannotated tool rather than
guess.

The table is here rather than on each decorator for one reason: the destructive
set is the part that matters, and it should be readable in one screen by someone
auditing what this server can do to their machine.

``meta`` falls back to the most cautious annotation for a name it does not know,
so a tool shipped without an entry is over-warned rather than under-warned.
``test_tool_annotations.py`` fails on that fallback, so the mistake is loud in
CI and safe in production.
"""
from __future__ import annotations

from typing import Any

#: Reads. Changes nothing, anywhere.
READ_ONLY = {
    "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False,
}
#: Reads, but the thing it reads is the user's repository, not vise's own store.
READ_ONLY_REPO = {**READ_ONLY, "openWorldHint": True}

#: Writes vise's own state. Calling it again does something further.
WRITES = {
    "readOnlyHint": False, "destructiveHint": False,
    "idempotentHint": False, "openWorldHint": False,
}
#: Writes vise's own state by setting a value. Calling it again changes nothing.
WRITES_ONCE = {**WRITES, "idempotentHint": True}
#: Writes to the user's repository.
WRITES_REPO = {**WRITES, "openWorldHint": True}

#: Drops state that cannot be recovered from inside vise.
DESTRUCTIVE = {
    "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": True, "openWorldHint": False,
}
#: Destroys work in the user's repository.
DESTRUCTIVE_REPO = {**DESTRUCTIVE, "openWorldHint": True}

#: What an unlisted tool gets: assume the worst on every axis.
UNKNOWN = {
    "readOnlyHint": False, "destructiveHint": True,
    "idempotentHint": False, "openWorldHint": True,
}

#: name -> (title shown in the approval prompt, annotations)
TOOLS: dict[str, tuple[str, dict[str, bool]]] = {
    # ── reads ────────────────────────────────────────────────────────────────
    "agent_list": ("List bundled subagents", READ_ONLY),
    "capability_audit": ("Show which capabilities are bound", READ_ONLY),
    "experience_derive_checklist": ("Derive a checklist from past experience", READ_ONLY),
    "experience_list": ("List recorded experience entries", READ_ONLY),
    "experience_stats": ("Show experience memory statistics", READ_ONLY),
    "goal_get": ("Show the active goal", READ_ONLY),
    "goal_validate": ("Check work against the active goal", READ_ONLY),
    "graph_builder_list": ("List draft workflows", READ_ONLY),
    "graph_builder_preview": ("Preview a draft workflow", READ_ONLY),
    "graph_check_phrase": ("Test a phrase against the workflow's edges", READ_ONLY),
    "graph_check_tool": ("Test a tool call against the workflow's edges", READ_ONLY),
    "graph_enforcer_status": ("Show whether the enforcer is on", READ_ONLY),
    "graph_get_ready_tasks": ("List tasks whose dependencies are met", READ_ONLY),
    "graph_list_available": ("List installable workflows", READ_ONLY),
    "graph_status": ("Show the active workflow's position", READ_ONLY),
    "graph_timeline": ("Show the workflow's transition history", READ_ONLY),
    "graph_validate": ("Validate a workflow definition", READ_ONLY),
    "graph_visualize": ("Render the workflow as a diagram", READ_ONLY),
    "recipe_describe": ("Describe a recipe", READ_ONLY),
    "recipe_list": ("List available recipes", READ_ONLY),
    # Returns a plan for the CALLER to execute; vise cannot call another MCP
    # server's tools, so this one runs nothing itself.
    "recipe_run": ("Resolve a recipe into a plan (executes nothing)", READ_ONLY),
    "run_budget": ("Show the run's budget", READ_ONLY),
    "run_explain": ("Explain how a task was routed", READ_ONLY),
    "run_list": ("List runs", READ_ONLY),
    "run_plan": ("Plan a node's work without dispatching it", READ_ONLY),
    "run_status": ("Show a run's status", READ_ONLY),
    "task_list": ("List the active workflow's tasks", READ_ONLY),
    "vise_version": ("Show the vise version", READ_ONLY),
    "snapshot_list": ("List snapshots", READ_ONLY_REPO),
    "snapshot_diff": ("Diff two snapshots", READ_ONLY_REPO),

    # ── writes ───────────────────────────────────────────────────────────────
    # Not read-only, despite a `# readOnlyHint: True` comment that sat above it
    # for releases: querying bumps FSRS stability and `last_reviewed` on every
    # entry it returns and saves the store. Reading this memory is how it learns
    # what is worth remembering.
    "experience_query": ("Query experience memory (records the recall)", WRITES),
    "experience_record": ("Record an experience entry", WRITES),
    "goal_bootstrap": ("Derive a goal from the current context", WRITES),
    "graph_builder_create": ("Create a draft workflow", WRITES),
    "graph_builder_add_node": ("Add a node to a draft workflow", WRITES),
    "graph_builder_add_edge": ("Add an edge to a draft workflow", WRITES),
    "graph_record_output": ("Record a node's output", WRITES),
    "graph_task_complete": ("Mark a task complete", WRITES),
    "graph_traverse": ("Move the workflow along an edge", WRITES),

    "capability_set": ("Bind or clear a capability", WRITES_ONCE),
    "goal_set": ("Set the active goal", WRITES_ONCE),
    "goal_complete": ("Close the active goal as done", WRITES_ONCE),
    "goal_abandon": ("Close the active goal as abandoned", WRITES_ONCE),
    "graph_activate": ("Activate a workflow", WRITES_ONCE),
    "graph_deactivate": ("Deactivate the workflow", WRITES_ONCE),
    "graph_set_node": ("Move the workflow to a node", WRITES_ONCE),
    "graph_enforcer_toggle": ("Turn the enforcer on or off", WRITES_ONCE),
    "graph_override_max_visits": ("Raise a node's visit limit", WRITES_ONCE),
    "graph_builder_update_node": ("Update a node in a draft workflow", WRITES_ONCE),
    "graph_builder_update_edge": ("Update an edge in a draft workflow", WRITES_ONCE),
    "graph_builder_save": ("Save a draft into the workflows library", WRITES_ONCE),
    "run_cancel": ("Ask a running scheduler to stop", WRITES_ONCE),

    "snapshot_create": ("Take a snapshot of the working tree", WRITES_REPO),

    # ── destroys ─────────────────────────────────────────────────────────────
    "goal_clear": ("Clear the active goal (discards it)", DESTRUCTIVE),
    "graph_reset": ("Reset the workflow (discards its state)", DESTRUCTIVE),
    "graph_builder_delete": ("Delete a draft workflow", DESTRUCTIVE),
    # `dry_run` defaults true, but a hint describes what the tool CAN do and the
    # person approving the call is looking at the arguments that turn it off.
    "snapshot_restore": (
        "Restore files from a snapshot (can overwrite the working tree)",
        DESTRUCTIVE_REPO,
    ),
}


def meta(name: str) -> dict[str, Any]:
    """Keyword arguments for ``mcp.tool`` describing *name*'s effect on the world."""
    title, annotations = TOOLS.get(name, (name.replace("_", " "), UNKNOWN))
    return {"title": title, "annotations": dict(annotations)}


def annotated(mcp):
    """``@annotated(mcp)`` in place of the bare tool decorator.

    It reads the tool's own ``__name__`` rather than taking the name again, so
    the table cannot drift from the function it describes — which is the failure
    mode every other duplicated fact in this repository has had.
    """
    def decorate(fn):
        return mcp.tool(**meta(fn.__name__))(fn)
    return decorate


__all__ = ["TOOLS", "UNKNOWN", "annotated", "meta"]
