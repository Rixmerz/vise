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


#: A still-open experience, and the entry type that would close it. The pairing
#: is by (file_pattern, domain), which is how `run_blocked` and `run_succeeded`
#: already meet — `runtime.lessons` files both under `run:<graph>:<node>`.
#:
#: `smell_introduced` maps to nothing on purpose. `smell_fixed`, `gate_resolved`
#: and `tension_resolved` are all in `VALID_TYPES` and **no writer in vise emits
#: any of them**, so pairing on them would mark every node-gate failure open
#: forever. Where a resolver is empty the window below is the only filter, which
#: is the honest answer: vise knows the failure happened and does not know that
#: it was fixed. The two names that are wired stay wired, so they work the day a
#: writer appears.
_OPEN_TYPES: dict[str, str] = {
    "run_blocked": "run_succeeded",
    "gate_blocked": "gate_resolved",
    "tension_caused": "tension_resolved",
    "smell_introduced": "",
}

_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def read_open_blockers(project_dir: str, *, days: float = 14.0,
                       limit: int = 5) -> list[dict]:
    """This project's still-open failures, worst and newest first. Never raises.

    Read straight off the project store's JSON rather than through
    ``ExperienceMemoryStore.query``. That path bumps FSRS recall and saves, so a
    hook calling it would raise the stability of whatever happened to be recent
    every time a session compacted — the store would learn from being read
    rather than from the work.

    An entry whose ``last_seen`` will not parse is dropped rather than kept. It
    is the opposite of the rule a gate follows, and for the opposite reason:
    nothing is being decided here, and an undated line cannot be ranked against
    dated ones without pretending to a position it has not earned.
    """
    try:
        from vise.engines.relevance import days_since
        from vise.hooks import _xdg

        store = _xdg.project_memory_path(project_dir)
        if not store.exists():
            return []
        entries = json.loads(store.read_bytes()).get("entries", [])
        if not isinstance(entries, list):
            return []

        closed = {
            (str(e.get("type") or ""), str(e.get("file_pattern") or ""),
             str(e.get("domain") or ""))
            for e in entries if isinstance(e, dict)
        }

        open_items: list[tuple[int, float, dict]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            etype = str(entry.get("type") or "")
            if etype not in _OPEN_TYPES:
                continue
            resolver = _OPEN_TYPES[etype]
            if resolver and (resolver, str(entry.get("file_pattern") or ""),
                             str(entry.get("domain") or "")) in closed:
                continue
            age = days_since(str(entry.get("last_seen") or ""), default=days + 1.0)
            if age > days:
                continue
            rank = _SEVERITY_RANK.get(str(entry.get("severity") or "medium"), 1)
            open_items.append((rank, -age, entry))

        open_items.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for _rank, _age, entry in open_items[:limit]]
    except Exception:
        return []


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
