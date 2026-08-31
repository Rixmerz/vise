"""Graph State — persistence layer for graph execution state.

Storage layout:
- State blob (graph_state.json): XDG — ~/.local/share/vise/states/<project>/
- Active graph.yaml: local — <project>/.claude/workflow/graph.yaml
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from vise.core import state_paths as _state_paths
from .graph_engine import GraphState, PathEntry, Graph
from vise.core.atomic import write_atomic

_SLUG_RE = re.compile(r'[^a-z0-9]+')


def _slugify(name: str) -> str:
    """Turn a display name into an id-safe slug (lowercase, hyphenated)."""
    slug = _SLUG_RE.sub('-', name.strip().lower()).strip('-')
    return slug or 'unnamed'


def _looks_like_display_name(value: str) -> bool:
    """Heuristic: a graph id is a filename stem (kebab-case, no spaces/caps).

    Anything with a space or uppercase letter is a display name, not an id.
    """
    return bool(value) and (' ' in value or value != value.lower())


def _resolve_display_name_to_id(project_dir: str, display_name: str) -> Optional[str]:
    """Best-effort: find the workflow-library graph id whose metadata name
    matches *display_name*. Read-only, tolerant — never raises, returns None
    on no match.
    """
    try:
        from vise.engines.workflow_scope import resolve_workflow_dirs
        from vise.engines.graph_parser import load_graph_from_file, GraphParseError
        for _scope, workflows_dir in resolve_workflow_dirs(project_dir):
            if not workflows_dir.exists():
                continue
            for yaml_file in workflows_dir.glob("*.yaml"):
                try:
                    candidate = load_graph_from_file(yaml_file)
                except GraphParseError:
                    continue
                if candidate.metadata.get('name') == display_name:
                    return yaml_file.stem
    except Exception:
        pass
    return None


def _get_centralized_state_dir(project_dir: str) -> Path:
    """Centralized state directory: ~/.local/share/vise/states/<project_name>/.

    Delegates to ``vise.core.state_paths.state_dir``. Kept for backward
    compatibility — all internal callers that do
    ``from vise.engines.graph_state import _get_centralized_state_dir``
    continue to work without changes.
    """
    return _state_paths.state_dir(project_dir)


def get_graph_state_file(project_dir: str) -> Path:
    """Get the graph state file path for a project (CENTRALIZED in hub)."""
    return _state_paths.graph_state_path(project_dir)


def get_graph_file(project_dir: str) -> Path:
    """Get the active graph file path for a project (LOCAL copy)."""
    return Path(project_dir) / ".claude" / "workflow" / "graph.yaml"


def load_graph_state(project_dir: str) -> GraphState:
    """Load graph state from file.

    Args:
        project_dir: Project directory path

    Returns:
        GraphState object (empty state if file doesn't exist)
    """
    state_file = get_graph_state_file(project_dir)

    if not state_file.exists():
        return GraphState()

    try:
        data = json.loads(state_file.read_text())

        # Parse execution path
        execution_path: list[PathEntry] = []
        for entry_data in data.get('execution_path', []):
            entry = PathEntry(
                from_node=entry_data.get('from_node'),
                to_node=entry_data.get('to_node', ''),
                edge_id=entry_data.get('edge_id'),
                timestamp=entry_data.get('timestamp', ''),
                reason=entry_data.get('reason', ''),
                commit_sha=entry_data.get('commit_sha'),
                outputs=entry_data.get('outputs') or None,
            )
            execution_path.append(entry)

        active_graph = data.get('active_graph')
        active_graph_name = data.get('active_graph_name')
        needs_normalize = False

        # Tolerant read: on-disk states from before active_graph was
        # canonicalized to an id may hold a display name (e.g. "Universal
        # Debug"). Resolve it to the real id so downstream lookups by id
        # keep working, and normalize the file on next write. Never drop
        # the active workflow just because the stored value is a name.
        if active_graph and _looks_like_display_name(active_graph):
            resolved_id = _resolve_display_name_to_id(project_dir, active_graph)
            if resolved_id:
                if not active_graph_name:
                    active_graph_name = active_graph
                active_graph = resolved_id
                needs_normalize = True

        state = GraphState(
            current_nodes=data.get('current_nodes', []),
            node_visits=data.get('node_visits', {}),
            execution_path=execution_path,
            active_graph=active_graph,
            active_graph_name=active_graph_name,
            max_visits_default=data.get('max_visits_default', 10),
            total_transitions=data.get('total_transitions', 0),
            last_activity=data.get('last_activity'),
            node_gate_state=data.get('node_gate_state', {}),
            baseline_smells=data.get('baseline_smells'),
            completed_tasks=data.get('completed_tasks', {}),
        )

        if needs_normalize:
            try:
                save_graph_state(project_dir, state)
            except Exception:
                pass  # normalization is best-effort; don't fail the read

        return state
    except Exception:
        return GraphState()


def save_graph_state(project_dir: str, state: GraphState):
    """Save graph state to file.

    Args:
        project_dir: Project directory path
        state: GraphState to save
    """
    state_file = get_graph_state_file(project_dir)

    # Ensure directory exists
    state_file.parent.mkdir(parents=True, exist_ok=True)

    # Serialize execution path
    execution_path_data = []
    for entry in state.execution_path:
        entry_dict: dict = {
            'from_node': entry.from_node,
            'to_node': entry.to_node,
            'edge_id': entry.edge_id,
            'timestamp': entry.timestamp,
            'reason': entry.reason,
            'commit_sha': entry.commit_sha,
        }
        if entry.outputs is not None:
            entry_dict['outputs'] = entry.outputs
        execution_path_data.append(entry_dict)

    # Update last_activity
    state.last_activity = datetime.now().isoformat()

    data = {
        'current_nodes': state.current_nodes,
        'node_visits': state.node_visits,
        'execution_path': execution_path_data,
        'active_graph': state.active_graph,
        'active_graph_name': state.active_graph_name,
        'max_visits_default': state.max_visits_default,
        'total_transitions': state.total_transitions,
        'last_activity': state.last_activity,
        'node_gate_state': state.node_gate_state,
        'completed_tasks': state.completed_tasks,
        **({'baseline_smells': state.baseline_smells} if state.baseline_smells is not None else {}),
    }

    write_atomic(state_file, json.dumps(data, indent=2))


def initialize_graph_state(project_dir: str, graph: Graph, graph_name: str) -> GraphState:
    """Initialize a new graph state for a given graph.

    Args:
        project_dir: Project directory path
        graph: The Graph to initialize state for
        graph_name: Name of the graph file (without extension), OR — from
            lazy-init call sites — the graph's display name. Either way,
            ``active_graph`` is always persisted as an id-shaped slug;
            the display name (from ``graph.metadata.name`` when available,
            else *graph_name* itself) is persisted separately as
            ``active_graph_name``.

    Returns:
        Newly initialized GraphState
    """
    start_node = graph.get_start_node()
    if not start_node:
        raise ValueError("Graph has no start node")

    # Lazy-init call sites pass the DISPLAY name (the local graph.yaml copy is
    # always named "graph.yaml", so its stem carries no id). Slugifying that
    # blindly invents an id that matches no library graph — "Universal Debug"
    # became "universal-debug" while the real id is "debug-graph". Ask the
    # library first; only fall back to a slug when nothing matches.
    if _looks_like_display_name(graph_name):
        graph_id = _resolve_display_name_to_id(project_dir, graph_name) or _slugify(graph_name)
    else:
        graph_id = graph_name
    display_name = graph.metadata.get('name') or graph_name

    state = GraphState(
        current_nodes=[start_node.id],
        node_visits={start_node.id: 1},
        execution_path=[
            PathEntry(
                from_node=None,
                to_node=start_node.id,
                edge_id=None,
                timestamp=datetime.now().isoformat(),
                reason="Graph initialized"
            )
        ],
        active_graph=graph_id,
        active_graph_name=display_name,
        max_visits_default=10,
        total_transitions=0,
        last_activity=datetime.now().isoformat()
    )

    save_graph_state(project_dir, state)
    return state


def reset_graph_state(project_dir: str, graph: Graph) -> GraphState:
    """Reset graph state to start node.

    Args:
        project_dir: Project directory path
        graph: The Graph to reset state for

    Returns:
        Reset GraphState
    """
    # Load existing state to preserve active_graph id + display name.
    # load_graph_state() already normalizes active_graph to an id via its
    # tolerant read path, so no re-slugify is needed here.
    existing = load_graph_state(project_dir)
    graph_name = existing.active_graph
    display_name = existing.active_graph_name or graph.metadata.get('name')

    start_node = graph.get_start_node()
    if not start_node:
        raise ValueError("Graph has no start node")

    state = GraphState(
        current_nodes=[start_node.id],
        node_visits={start_node.id: 1},
        execution_path=[
            PathEntry(
                from_node=None,
                to_node=start_node.id,
                edge_id=None,
                timestamp=datetime.now().isoformat(),
                reason="Graph reset"
            )
        ],
        active_graph=graph_name,
        active_graph_name=display_name,
        max_visits_default=existing.max_visits_default,
        total_transitions=0,
        last_activity=datetime.now().isoformat()
    )

    save_graph_state(project_dir, state)
    return state


class NoActiveWorkflowError(ValueError):
    """Raised when a project has no active workflow.

    Subclasses ValueError so existing ``except ValueError`` call sites keep
    working; catch it specifically to render a friendlier answer.
    """


def load_active_graph(project_dir: str):
    """Load the active graph + state, or raise if none is active.

    Single source of truth for four modules that each carried their own copy
    of this, "intentionally duplicated ... to keep each module independently
    importable". Importing one function from `engines` costs nothing and keeps
    them independent; four hand-synced copies is the same arrangement that
    silently split the state tree in two (see hooks/_xdg.py).

    Lazy-init is preserved for a project that has NEVER had state — that is a
    convenience, and initializing on first touch is reasonable. It is NOT
    applied when a state file exists and says nothing is active: that is a
    deliberate `graph_deactivate`, and re-initializing it resurrected the very
    workflow the user just ended. The first `graph_status` after deactivating
    silently brought it back, gate and all.
    """
    from vise.engines.graph_parser import load_graph_from_file

    graph_file = get_graph_file(project_dir)
    if not graph_file.exists():
        raise NoActiveWorkflowError(f"No graph.yaml found at {graph_file}")

    graph = load_graph_from_file(graph_file)
    state = load_graph_state(project_dir)

    if not state.current_nodes:
        if get_graph_state_file(project_dir).exists() and not state.active_graph:
            raise NoActiveWorkflowError(
                "No active workflow (deactivated). "
                "Run graph_activate to start one."
            )
        state = initialize_graph_state(
            project_dir, graph, graph.metadata.get('name', 'unnamed')
        )

    return graph, state


def deactivate_graph_state(project_dir: str) -> dict:
    """End the active workflow: no graph active, nothing gated.

    A workflow could be started but never finished. `reset_graph_state` returns
    to the START node, which in every bundled workflow is the most restrictive
    one — so reset re-arms the gate instead of releasing it. The only other exit
    was disabling the enforcer, which its own docstring says does NOT clear
    state, so it silently mutes gating for every FUTURE workflow too. Result: a
    workflow abandoned in one session blocks unrelated work in the next, with no
    honest way out. Nobody hit this while the enforcer was broken and never
    blocked anything (see hooks/_xdg.py); it surfaced the first time the gate
    actually fired — and the bundled debug workflow has no node at all from
    which the trap could be fixed, since even its terminal node blocks Edit.

    Execution history is preserved: this records that the workflow ended, it
    does not pretend it never ran.
    """
    state = load_graph_state(project_dir)
    ended = state.active_graph
    if not ended:
        return {"deactivated": False, "reason": "no active workflow"}

    state.execution_path.append(
        PathEntry(
            from_node=state.current_nodes[0] if state.current_nodes else None,
            to_node=None,
            edge_id=None,
            timestamp=datetime.now().isoformat(),
            reason=f"Workflow '{ended}' deactivated",
        )
    )
    display = state.active_graph_name
    state.active_graph = None
    state.active_graph_name = None
    state.current_nodes = []
    state.last_activity = datetime.now().isoformat()
    save_graph_state(project_dir, state)
    return {"deactivated": True, "was_active": ended, "was_active_name": display}


def get_node_visit_warning(state: GraphState, node_id: str, max_visits: int) -> Optional[str]:
    """Check if a node is approaching its max visits limit.

    Args:
        state: Current graph state
        node_id: Node to check
        max_visits: Maximum visits allowed

    Returns:
        Warning message if at 80%+ of limit, None otherwise
    """
    current_visits = state.get_visit_count(node_id)
    threshold = max_visits * 0.8

    if current_visits >= max_visits:
        return f"BLOCKED: Node '{node_id}' has reached max visits ({current_visits}/{max_visits})"
    elif current_visits >= threshold:
        remaining = max_visits - current_visits
        return f"WARNING: Node '{node_id}' approaching max visits ({current_visits}/{max_visits}, {remaining} remaining)"

    return None
