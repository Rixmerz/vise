"""Shared utilities for AgentCockpit hooks."""
import json
import re
from pathlib import Path


def read_active_state(project_dir: str) -> dict:
    """Read active workflow + goal state from disk (fail-open, read-only).

    Returns a dict with any of: workflow, current_node, tools_blocked,
    pending_validators, goal, goal_confidence, goal_target. Empty dict when
    nothing is active. Never raises.
    """
    out: dict = {}
    try:
        from vise.core import state_paths
        state_file = state_paths.graph_state_path(project_dir)
        if state_file.exists():
            data = json.loads(state_file.read_text())
            active = data.get("active_graph")
            nodes = data.get("current_nodes", [])
            if active and nodes:
                out["workflow"] = active
                out["current_node"] = nodes[0]
                gate = data.get("node_gate_state") or {}
                pending = gate.get(nodes[0])
                if pending:
                    out["pending_validators"] = pending
                # Blocked tools for the current node, from local graph.yaml
                try:
                    from vise.hooks.graph_enforcer import parse_tools_blocked
                    graph_file = (Path(project_dir) / ".claude" / "workflow"
                                  / "graph.yaml")
                    if graph_file.exists():
                        blocked = parse_tools_blocked(graph_file.read_text())
                        tb = blocked.get(nodes[0])
                        if tb:
                            out["tools_blocked"] = tb
                except Exception:
                    pass
    except Exception:
        pass
    try:
        from vise.engines import goal_state
        goal = goal_state.get_goal(project_dir)
        if goal and goal.status == "active":
            out["goal"] = goal.goal
            out["goal_confidence"] = goal.confidence
            out["goal_target"] = goal.target_confidence
    except Exception:
        pass
    return out

_DOMAIN_MAP = {
    "auth": ["auth", "login", "session", "token", "jwt"],
    "api": ["api", "endpoint", "route", "controller", "handler", "middleware"],
    "ui": ["component", "page", "view", "layout", "modal", "form", "panel"],
    "config": ["config", "setting", "env", "constant"],
    "data": ["model", "schema", "entity", "migration", "repository", "store"],
    "style": ["style", "css", "theme"],
    "util": ["util", "helper", "lib", "common", "shared"],
}


def extract_keywords(path: str) -> list[str]:
    """Extract keywords from a file path."""
    stem = Path(path).stem.lower()
    words = re.split(r'(?<=[a-z])(?=[A-Z])|[-_./\\]', stem)
    words = [w.lower() for w in words if len(w) > 1]
    parent = Path(path).parent.name.lower()
    if parent and len(parent) > 1 and parent not in (".", "src"):
        words.append(parent)
    return list(dict.fromkeys(words))  # dedupe preserving order


def guess_domain(path: str) -> str:
    """Guess domain from file path."""
    lower = path.lower()
    best, best_score = "", 0
    for domain, kws in _DOMAIN_MAP.items():
        score = sum(1 for kw in kws if kw in lower)
        if score > best_score:
            best_score = score
            best = domain
    return best or "general"
