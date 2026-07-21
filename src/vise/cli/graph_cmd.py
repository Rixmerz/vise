"""`vise graph` — out-of-band graph state management.

Subcommands that operate directly on the on-disk graph state file
(``~/.local/share/vise/states/<project>/graph_state.json``) without
requiring a running MCP server. Intended as a recovery escape hatch for
the deadlock pattern:

    1. User activates a graph workflow whose current phase blocks Bash
       / Edit / Write.
    2. MCP server disconnects (transient bug, OOM, harness restart).
    3. The PreToolUse ``graph_enforcer`` hook keeps reading the persisted
       state and keeps blocking — correctly, from its point of view.
    4. Without MCP access the user cannot call ``graph_reset`` to clear
       the state, so every mutating tool is blocked.

``vise graph reset`` writes a cleared state blob to disk so the hook
starts approving again. Run from any terminal; no MCP needed.

``vise graph run`` is the unattended driver for cyclable workflows. It
spawns a headless ``claude -p`` session whose first action activates the
named workflow and then traverses it to completion. The graph's own
back-edges, max_visits, and node_gate validators drive the loop; the
goal_gate Stop hook is the safety net. It does NOT execute node work
itself — that is delegated to the spawned agent session.

Usage::

    vise graph run feature-dev-graph [--project /path/to/project]
    vise graph run feature-dev-graph --emit   # print command, don't spawn
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from vise.engines.graph_state import get_graph_state_file

# ---------------------------------------------------------------------------
# graph run — unattended driver constants
# ---------------------------------------------------------------------------

_ENV_INNER = "VISE_GRAPH_RUN_INNER"

_BOOTSTRAP_PROMPT_TEMPLATE = """\
You are an unattended workflow driver for project: {project_dir}

Your task: drive the workflow '{workflow_name}' to completion. Work autonomously; do not pause to ask the user questions.

Steps:
1. Call graph_activate(graph_name='{workflow_name}', project_dir='{project_dir}') to activate the workflow.
2. Read the returned prompt_injection and briefing carefully.
3. Do the work the current phase requires (implement, review, test, etc.).
4. Call graph_traverse(direction='next', project_dir='{project_dir}') to advance to the next phase.
5. Repeat steps 2–4 until the workflow reaches a terminal node or the goal_gate Stop hook fires.

Cycle control is provided by the graph's back-edges, max_visits settings, and node_gate validators — you do not need to implement any loop logic yourself. goal_gate is your safety net: if it blocks the session, the run ends cleanly.

Begin now.
"""


def _find_workflow(workflow_name: str, project_dir: Path) -> Path | None:
    """Resolve a workflow name using the 3-scope cascade: project → user → bundled.

    Tries both ``<name>-graph.yaml`` and ``<name>.yaml`` at each scope so
    users can pass either ``feature-dev`` or ``feature-dev-graph`` and reach
    the same file — matching the same search strategy used by ``graph_activate``
    inside the MCP layer.
    """
    bundled_dir = Path(__file__).resolve().parent.parent / "assets" / "workflows"
    scopes = [
        project_dir / ".claude" / "workflows",
        Path.home() / ".vise" / "workflows",
        bundled_dir,
    ]
    suffixes = ["-graph.yaml", ".yaml"]
    for scope in scopes:
        for suffix in suffixes:
            candidate = scope / f"{workflow_name}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def _resolve_project_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    return Path.cwd().resolve()


# ---------------------------------------------------------------------------
# graph run — command implementation
# ---------------------------------------------------------------------------

def _cmd_run(args: argparse.Namespace) -> int:
    """Launch (or emit) a headless claude session that drives a cyclable workflow.

    With ``--emit`` / ``--dry-run``: prints the constructed shell command to
    stdout so it can be pasted into a crontab or systemd unit without
    spawning a real session. This is also the safe path for tests.

    Without those flags: spawns ``claude -p <prompt>`` as a subprocess with
    ``VISE_AUTONOMY=1`` and ``VISE_GOAL_GATE=1`` armed so the graph's enforcer
    and the goal_gate Stop hook are both active. Node work is performed by the
    spawned agent session — this driver does NOT execute it.

    Manual-verification GAP: whether a headless ``claude -p`` session reliably
    honours the PreToolUse graph_enforcer and the goal_gate Stop hook over a
    back-edge cycle needs a live smoke test (spawn ``claude -p`` against a
    2-node back-edge graph and confirm the Stop gate fires). See notion
    ``scheduled-workflow-design.md`` ``unknown:`` section. Do not call this
    done until that smoke passes in a real session.
    """
    project_dir = _resolve_project_dir(getattr(args, "project", None))
    workflow_name: str = args.workflow

    # Recursion guard — bail if we are already inside a spawned run.
    if os.environ.get(_ENV_INNER) == "1":
        print(
            "[vise graph run] Refusing to nest: VISE_GRAPH_RUN_INNER is already set.",
            file=sys.stderr,
        )
        return 1

    # Resolve the workflow across the 3-scope cascade.
    workflow_path = _find_workflow(workflow_name, project_dir)
    if workflow_path is None:
        print(
            f"[vise graph run] Workflow '{workflow_name}' not found.",
            file=sys.stderr,
        )
        print(
            "[vise graph run] Searched (project→user→bundled):",
            file=sys.stderr,
        )
        print(
            f"  {project_dir}/.claude/workflows/{workflow_name}[-graph].yaml",
            file=sys.stderr,
        )
        print(
            f"  ~/.vise/workflows/{workflow_name}[-graph].yaml",
            file=sys.stderr,
        )
        print(
            "  <vise-bundled>/assets/workflows/<name>[-graph].yaml",
            file=sys.stderr,
        )
        return 1

    prompt = _BOOTSTRAP_PROMPT_TEMPLATE.format(
        project_dir=str(project_dir),
        workflow_name=workflow_name,
    )
    # --dangerously-skip-permissions auto-approves the spawned inner `claude -p`
    # session ONLY: this driver is unattended/scheduled — no human to answer a
    # permission prompt. Live-proven WITH the flag in graph-run-f3-verify.md — on
    # an untrusted project a gated Bash runs with the flag, is DENIED without it,
    # while graph_enforcer + goal_gate hooks still fire (the flag removes the
    # human prompt, not the hook guardrails). Load-bearing under standard
    # ask-mode; redundant when a global permissions.defaultMode auto/bypass is set.
    cmd = ["claude", "--dangerously-skip-permissions", "-p", prompt]

    if getattr(args, "emit", False) or getattr(args, "dry_run", False):
        print(shlex.join(cmd))
        return 0

    # Arm autonomy rails and recursion guard, then spawn.
    env = os.environ.copy()
    env[_ENV_INNER] = "1"
    env["VISE_AUTONOMY"] = "1"
    env["VISE_GOAL_GATE"] = "1"

    try:
        result = subprocess.run(cmd, env=env, cwd=str(project_dir), shell=False)
        return result.returncode
    except FileNotFoundError:
        print(
            "[vise graph run] 'claude' CLI not found in PATH. "
            "Install the Claude Code CLI or override via VISE_JUDGE_CMD.",
            file=sys.stderr,
        )
        return 1


def _cmd_reset(args: argparse.Namespace) -> int:
    project_dir = _resolve_project_dir(args.project)
    state_file = get_graph_state_file(str(project_dir))

    if not state_file.exists():
        print(f"[vise graph reset] No active graph state at {state_file}")
        print("[vise graph reset] Nothing to reset.")
        return 0

    try:
        previous = json.loads(state_file.read_text())
        prev_graph = previous.get("active_graph")
        prev_nodes = previous.get("current_nodes", [])
    except Exception:
        prev_graph = None
        prev_nodes = []

    cleared = {
        "current_nodes": [],
        "node_visits": {},
        "execution_path": [],
        "active_graph": None,
        "max_visits_default": 10,
        "total_transitions": 0,
        "last_activity": None,
        "tension_gate_state": {},
        "last_dcc_result": None,
        "last_dcc_timestamp": None,
        "completed_tasks": {},
    }

    if args.dry_run:
        print(f"[vise graph reset] (dry-run) would clear: {state_file}")
        if prev_graph:
            print(f"[vise graph reset]   active_graph: {prev_graph}")
            print(f"[vise graph reset]   current_nodes: {prev_nodes}")
        return 0

    state_file.write_text(json.dumps(cleared, indent=2))

    print(f"[vise graph reset] Cleared {state_file}")
    if prev_graph:
        print(
            f"[vise graph reset] Was: active_graph={prev_graph!r}, "
            f"current_nodes={prev_nodes}"
        )
    print(
        "[vise graph reset] PreToolUse hooks will now approve. "
        "Re-activate a workflow with graph_activate when ready."
    )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    project_dir = _resolve_project_dir(args.project)
    state_file = get_graph_state_file(str(project_dir))

    if not state_file.exists():
        print(f"[vise graph status] No state file at {state_file}")
        return 0

    try:
        state = json.loads(state_file.read_text())
    except Exception as e:
        print(f"[vise graph status] State file unreadable: {e}")
        return 1

    print(f"[vise graph status] {state_file}")
    print(f"  active_graph:  {state.get('active_graph') or '(none)'}")
    print(f"  current_nodes: {state.get('current_nodes') or []}")
    print(f"  last_activity: {state.get('last_activity') or '(never)'}")
    print(f"  transitions:   {state.get('total_transitions', 0)}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    """Register the `vise graph` subcommand tree."""
    graph = sub.add_parser(
        "graph",
        help="Out-of-band graph state management and unattended workflow driver",
    )
    graph_sub = graph.add_subparsers(dest="graph_command", metavar="SUBCOMMAND")

    reset = graph_sub.add_parser(
        "reset",
        help=(
            "Clear active graph state without needing the MCP server. "
            "Use to recover from a deadlock when the PreToolUse hook is "
            "blocking and graph_reset via MCP is unreachable."
        ),
    )
    reset.add_argument(
        "--project",
        default=None,
        help="Project directory (default: current working directory)",
    )
    reset.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be cleared without writing",
    )
    reset.set_defaults(func=_cmd_reset)

    status = graph_sub.add_parser(
        "status",
        help="Read the on-disk graph state without needing the MCP server",
    )
    status.add_argument(
        "--project",
        default=None,
        help="Project directory (default: current working directory)",
    )
    status.set_defaults(func=_cmd_status)

    run = graph_sub.add_parser(
        "run",
        help=(
            "Spawn a headless 'claude -p' session that activates WORKFLOW and "
            "traverses it to completion. The graph's back-edges/max_visits/node_gate "
            "validators drive any cyclable loops; goal_gate is the safety net. "
            "Use --emit to print the command instead of launching it."
        ),
    )
    run.add_argument(
        "workflow",
        help=(
            "Workflow name (e.g. 'feature-dev-graph' or 'feature-dev'). "
            "Searched across project/.claude/workflows/, ~/.vise/workflows/, "
            "and vise's bundled workflows."
        ),
    )
    run.add_argument(
        "--project",
        default=None,
        dest="project",
        metavar="DIR",
        help="Project directory (default: current working directory)",
    )
    run.add_argument(
        "--emit",
        action="store_true",
        help="Print the constructed claude command to stdout instead of spawning it",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Alias for --emit",
    )
    run.set_defaults(func=_cmd_run)

    def _no_subcommand(_a: argparse.Namespace) -> int:
        graph.print_help()
        return 1

    graph.set_defaults(func=_no_subcommand)
