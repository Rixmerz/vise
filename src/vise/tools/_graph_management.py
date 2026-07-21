"""Graph management tools: graph_list_available, graph_activate, graph_visualize,
graph_timeline, graph_validate, graph_override_max_visits.
"""

import subprocess
import sys
from pathlib import Path

from vise.core.session import resolve_project_dir
from vise.engines.workflow_scope import resolve_workflow_dirs
from vise.engines.graph_engine import Graph, GraphState, generate_mermaid
from vise.engines.graph_parser import load_graph_from_file, GraphParseError
from vise.engines.graph_state import (
    load_graph_state, initialize_graph_state,
    get_graph_file,
)


def _load_active_graph(project_dir: str) -> tuple[Graph, GraphState]:
    """Load active graph and state for a project."""
    graph_file = get_graph_file(project_dir)
    if not graph_file.exists():
        raise ValueError(f"No graph.yaml found at {graph_file}")

    graph = load_graph_from_file(graph_file)
    state = load_graph_state(project_dir)

    if not state.current_nodes:
        graph_name = graph.metadata.get('name', 'unnamed')
        state = initialize_graph_state(project_dir, graph, graph_name)

    return graph, state


def register_graph_management_tools(mcp):

    @mcp.tool()
    def graph_list_available(project_dir: str | None = None, session_id: str | None = None) -> dict:
        # readOnlyHint: True
        """List all available graphs in the project's workflows library.

        Args:
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        # Collect graphs from all three scopes (lowest precedence first).
        # Project scope wins over user wins over bundled (same name → higher scope entry kept).
        seen: dict[str, dict] = {}  # graph_stem -> entry dict

        for scope, workflows_dir in resolve_workflow_dirs(resolved_dir):
            if not workflows_dir.exists():
                continue
            candidates = sorted({*workflows_dir.glob("*-graph.yaml"), *workflows_dir.glob("*.yaml")})
            for yaml_file in candidates:
                graph_name = yaml_file.stem
                try:
                    content = yaml_file.read_text()
                    # Skip non-graph YAML: require both ``nodes:`` and ``edges:``
                    # at the start of a line (top-level sections).
                    if "\nnodes:" not in "\n" + content or "\nedges:" not in "\n" + content:
                        continue
                    name = graph_name
                    description = ""
                    version = ""
                    raw_lines = content.split('\n')
                    # Build a parser that understands the two YAML shapes vise
                    # emits — builder ``metadata:`` block and demo-style flat
                    # top-level keys — and the block-scalar (``|`` / ``>``)
                    # form, since ``demo-feature.yaml`` uses
                    # ``description: |`` with indented continuation lines.
                    in_metadata = False
                    i = 0
                    while i < len(raw_lines):
                        line = raw_lines[i]
                        if line and not line[0].isspace():
                            stripped_top = line.strip()
                            if stripped_top.startswith('metadata:'):
                                in_metadata = True
                                i += 1
                                continue
                            in_metadata = False
                            key_match = None
                            for k in ('name', 'description', 'version'):
                                if stripped_top.startswith(f'{k}:'):
                                    key_match = k
                                    break
                            if key_match is None:
                                i += 1
                                continue
                            val = stripped_top.split(':', 1)[1].strip().strip('"').strip("'")
                            if val in ('|', '>'):
                                block: list[str] = []
                                i += 1
                                while i < len(raw_lines):
                                    nxt = raw_lines[i]
                                    if nxt and not nxt[0].isspace():
                                        break
                                    block.append(nxt.strip())
                                    i += 1
                                val = ' '.join(seg for seg in block if seg).strip()
                            else:
                                i += 1
                            if key_match == 'name' and (not name or name == graph_name):
                                name = val
                            elif key_match == 'description' and not description:
                                description = val
                            elif key_match == 'version' and not version:
                                version = val
                            continue
                        if not in_metadata:
                            i += 1
                            continue
                        stripped = line.strip()
                        key_match = None
                        for k in ('name', 'description', 'version'):
                            if stripped.startswith(f'{k}:'):
                                key_match = k
                                break
                        if key_match is None:
                            i += 1
                            continue
                        val = stripped.split(':', 1)[1].strip().strip('"').strip("'")
                        if val in ('|', '>'):
                            # Block scalar — take continuation lines that are
                            # more indented than the key itself.
                            base_indent = len(line) - len(line.lstrip())
                            block = []
                            i += 1
                            while i < len(raw_lines):
                                nxt = raw_lines[i]
                                if not nxt.strip():
                                    i += 1
                                    continue
                                this_indent = len(nxt) - len(nxt.lstrip())
                                if this_indent <= base_indent:
                                    break
                                block.append(nxt.strip())
                                i += 1
                            val = ' '.join(seg for seg in block if seg).strip()
                        else:
                            i += 1
                        if key_match == 'name':
                            name = val
                        elif key_match == 'description':
                            description = val
                        elif key_match == 'version':
                            version = val
                    seen[graph_name] = {
                        "id": graph_name,
                        "name": name,
                        "description": description,
                        "version": version,
                        "file": str(yaml_file),
                        "type": "graph",
                        "scope": scope,
                    }
                except Exception as e:
                    print(f"[vise] Warning: failed to parse graph YAML '{yaml_file}': {e}", file=sys.stderr)
                    seen[graph_name] = {
                        "id": graph_name,
                        "name": graph_name,
                        "file": str(yaml_file),
                        "type": "graph",
                        "scope": scope,
                    }

        graphs = list(seen.values())

        return {
            "success": True,
            "session_id": sid,
            "graphs": graphs,
            "total": len(graphs),
            "project_dir": resolved_dir
        }

    @mcp.tool()
    async def graph_activate(
        graph_name: str,
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # destructiveHint: True (replaces active graph)
        """Activate a graph from the workflows library.

        Copies the graph YAML to graph.yaml and initializes state.

        Args:
            graph_name: Name of the graph file (without -graph.yaml extension)
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        # Search scopes from highest to lowest precedence: project → user → bundled
        graph_file = None
        for scope, workflows_dir in reversed(resolve_workflow_dirs(resolved_dir)):
            candidate = workflows_dir / f"{graph_name}-graph.yaml"
            if candidate.exists():
                graph_file = candidate
                break
            candidate = workflows_dir / f"{graph_name}.yaml"
            if candidate.exists():
                graph_file = candidate
                break

        if graph_file is None:
            # Collect all available graphs across scopes for the error message
            available: list[str] = []
            seen_available: set[str] = set()
            for _scope, workflows_dir in reversed(resolve_workflow_dirs(resolved_dir)):
                if not workflows_dir.exists():
                    continue
                for f in sorted(workflows_dir.glob("*-graph.yaml")):
                    if f.stem not in seen_available:
                        available.append(f.stem)
                        seen_available.add(f.stem)
                for f in sorted(workflows_dir.glob("*.yaml")):
                    if f.stem not in seen_available:
                        available.append(f.stem)
                        seen_available.add(f.stem)
            return {
                "success": False,
                "session_id": sid,
                "message": f"Graph '{graph_name}' not found",
                "available_graphs": available,
                "project_dir": resolved_dir
            }

        # Parse to validate
        try:
            graph = load_graph_from_file(graph_file)
        except GraphParseError as e:
            return {
                "success": False,
                "session_id": sid,
                "message": f"Invalid graph: {e}",
                "project_dir": resolved_dir
            }

        # Copy to active graph.yaml
        target_file = get_graph_file(resolved_dir)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(graph_file.read_text())

        # Initialize state
        initialize_graph_state(resolved_dir, graph, graph_name)
        start_node = graph.get_start_node()

        # Auto-refresh project metadata and pattern catalog on activation
        try:
            from vise.engines.graph_state import _get_centralized_state_dir
            state_dir = str(_get_centralized_state_dir(resolved_dir))
        except Exception:
            state_dir = str(Path(resolved_dir) / ".claude" / "workflow")

        try:
            from vise.engines.project_metadata import ProjectMetadata
            pm = ProjectMetadata(resolved_dir)
            pm.discover_all()
            pm.save(state_dir)
        except Exception as e:
            print(f"[vise] Metadata refresh failed (non-fatal): {e}", file=sys.stderr)

        try:
            from vise.engines.pattern_catalog import PatternCatalog
            pc = PatternCatalog(resolved_dir)
            pc.discover_all()
            pc.save(state_dir)
        except Exception as e:
            print(f"[vise] Pattern catalog refresh failed (non-fatal): {e}", file=sys.stderr)

        return {
            "success": True,
            "session_id": sid,
            "message": f"Graph '{graph_name}' activated",
            "graph_name": graph.metadata.get('name', graph_name),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "current_node": {
                "id": start_node.id if start_node else None,
                "name": start_node.name if start_node else None
            },
            "prompt_injection": start_node.prompt_injection if start_node else None,
            "project_dir": resolved_dir
        }

    @mcp.tool()
    def graph_visualize(project_dir: str | None = None, session_id: str | None = None) -> dict:
        # readOnlyHint: True
        """Generate Mermaid diagram of the graph.

        Returns a Mermaid flowchart that can be rendered in markdown.
        Current node is highlighted in green.

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

        mermaid = generate_mermaid(graph, state)

        return {
            "success": True,
            "session_id": sid,
            "graph_name": state.active_graph or graph.metadata.get('name', 'unnamed'),
            "mermaid": mermaid,
            "hint": "Render this in a markdown code block with ```mermaid",
            "project_dir": resolved_dir
        }

    @mcp.tool()
    async def graph_timeline(
        since: str | None = None,
        limit: int = 50,
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # readOnlyHint: True
        """Get a unified timeline of workflow transitions and git commits.

        Correlates three data sources into a single chronological view:
        - Workflow transitions (from execution_path in graph state)
        - Git commits (from git log)

        Args:
            since: ISO timestamp to filter events from (default: workflow start)
            limit: Maximum number of events to return (default: 50)
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

        events = []

        # 1. Workflow transitions from execution_path
        for entry in state.execution_path:
            ts = entry.timestamp if hasattr(entry, 'timestamp') else entry.get("timestamp", "")
            from_node = entry.from_node if hasattr(entry, 'from_node') else entry.get("from_node")
            to_node = entry.to_node if hasattr(entry, 'to_node') else entry.get("to_node", "?")
            reason = entry.reason if hasattr(entry, 'reason') else entry.get("reason", "")
            edge_id_val = entry.edge_id if hasattr(entry, 'edge_id') else entry.get("edge_id")

            if since and ts < since:
                continue

            to_name = graph.nodes[to_node].name if to_node in graph.nodes else to_node
            events.append({
                "type": "transition",
                "timestamp": ts,
                "description": f"-> {to_name}" + (f" ({reason})" if reason else ""),
                "from_node": from_node,
                "to_node": to_node,
                "edge_id": edge_id_val,
            })

        # 2. Git commits
        since_flag = f"--since={since}" if since else "--since=7 days ago"
        try:
            git_result = subprocess.run(
                ["git", "log", since_flag, "--format=%H|%aI|%s", f"--max-count={limit}"],
                cwd=resolved_dir, capture_output=True, text=True, timeout=10
            )
            for line in git_result.stdout.strip().split("\n"):
                if not line or "|" not in line:
                    continue
                parts = line.split("|", 2)
                if len(parts) < 3:
                    continue
                commit_hash, ts, message = parts

                # Get changed files for this commit
                diff_result = subprocess.run(
                    ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash],
                    cwd=resolved_dir, capture_output=True, text=True, timeout=10
                )
                files = [f.strip() for f in diff_result.stdout.strip().split("\n") if f.strip()]

                events.append({
                    "type": "commit",
                    "timestamp": ts,
                    "description": message,
                    "commit": commit_hash[:8],
                    "files": files[:10],
                })
        except Exception as e:
            print(f"[vise] Warning: failed to parse git log for timeline: {e}", file=sys.stderr)
            pass

        # Sort by timestamp (events without timestamps go last)
        events.sort(key=lambda e: e.get("timestamp") or "9999", reverse=False)

        return {
            "success": True,
            "session_id": sid,
            "total_events": len(events),
            "events": events[:limit],
            "event_counts": {
                "transitions": sum(1 for e in events if e["type"] == "transition"),
                "commits": sum(1 for e in events if e["type"] == "commit"),
            },
            "project_dir": resolved_dir,
        }

    @mcp.tool()
    def graph_validate(project_dir: str | None = None, session_id: str | None = None) -> dict:
        # readOnlyHint: True
        """Validate the current graph structure.

        Checks for orphan nodes, missing references, and other structural issues.

        Args:
            project_dir: Absolute path to the project directory (optional after set_session)
            session_id: Optional session ID for parallel session isolation
        """
        resolved_dir, sid = resolve_project_dir(project_dir, session_id)

        graph_file = get_graph_file(resolved_dir)
        if not graph_file.exists():
            return {
                "valid": False,
                "session_id": sid,
                "message": "No graph.yaml found",
                "project_dir": resolved_dir
            }

        try:
            graph = load_graph_from_file(graph_file)
        except GraphParseError as e:
            return {
                "valid": False,
                "session_id": sid,
                "errors": [str(e)],
                "project_dir": resolved_dir
            }

        errors = graph.validate()

        return {
            "valid": len(errors) == 0,
            "session_id": sid,
            "graph_name": graph.metadata.get('name', 'unnamed'),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "errors": errors if errors else None,
            "project_dir": resolved_dir
        }

    @mcp.tool()
    def graph_override_max_visits(
        node_id: str,
        new_max: int,
        project_dir: str | None = None,
        session_id: str | None = None
    ) -> dict:
        # destructiveHint: True (modifies visit limits)
        """Override max_visits for a specific node (escape hatch for loops).

        Use this when you need to exceed a node's visit limit for legitimate reasons.
        The override only affects the in-memory graph state for this session.

        Args:
            node_id: ID of the node to override
            new_max: New maximum visits (must be > current visits)
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
                "project_dir": resolved_dir
            }

        current_visits = state.get_visit_count(node_id)
        if new_max <= current_visits:
            return {
                "error": True,
                "session_id": sid,
                "message": f"new_max ({new_max}) must be greater than current visits ({current_visits})",
                "project_dir": resolved_dir
            }

        # Update the node's max_visits (in-memory only - doesn't persist to YAML)
        graph.nodes[node_id].max_visits = new_max

        return {
            "success": True,
            "session_id": sid,
            "message": f"Node '{node_id}' max_visits updated to {new_max}",
            "node_id": node_id,
            "current_visits": current_visits,
            "new_max_visits": new_max,
            "warning": "This override is in-memory only and will reset when graph is reloaded",
            "project_dir": resolved_dir
        }
