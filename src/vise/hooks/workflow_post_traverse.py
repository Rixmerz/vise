#!/usr/bin/env python3
"""Workflow Post-Traverse Hook — PostToolUse for graph_traverse.

Fires after every graph_traverse MCP call and records transition experience
data into the project experience_memory.json file.

Protocol:
  stdin:  {"tool_name": "mcp__vise__graph_traverse", "tool_result": {...}}
  env:    CLAUDE_PROJECT_DIR
  stdout: {"decision": "approve"}
  stderr: brief transition summary
  exit 0: always
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from vise.hooks import _xdg

_APPROVE = json.dumps({"decision": "approve"})


def _get_changed_files(project_path: str) -> list[str]:
    """Return list of files changed in the last commit (relative paths)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, cwd=project_path, timeout=5
        )
        if result.returncode == 0:
            files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
            return files
    except Exception:
        pass
    return []


def _record_experience(result: dict, project_path: str) -> None:
    """Persist experience entries from graph_traverse result using ExperienceMemoryStore."""
    from_node = result.get("from_node", "")
    to_node = result.get("to_node", "")
    edge_id = result.get("traversed_edge", "")
    reason = result.get("reason", "")

    if not from_node and not to_node:
        return

    smells_summary = ""

    # Try to import experience_memory from vise
    wm_src = _xdg.src_dir()
    # Also try project-local install
    proj_wm_src = Path(project_path) / ".vise" / "src"
    for src_path in [proj_wm_src, wm_src]:
        if src_path.exists() and str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))

    try:
        from workflow_manager.experience_memory import (
            ExperienceEntry, ExperienceMemoryStore,
            generalize_path, extract_file_keywords, guess_domain,
        )
    except ImportError:
        _record_experience_fallback(result, project_path,
                                     from_node, to_node, edge_id, reason,
                                     smells_summary)
        return

    project_name = Path(project_path).name
    store = ExperienceMemoryStore()
    store.load(scope="project", project_name=project_name, project_dir=project_path)

    changed_files = _get_changed_files(project_path)

    if changed_files:
        for rel_file in changed_files:
            entry_type = "smell_introduced" if smells_summary else "impact_high"
            description = f"{from_node} → {to_node}: {smells_summary[:120]}" if smells_summary else f"{from_node} → {to_node}"
            entry = ExperienceEntry(
                type=entry_type,
                file_pattern=generalize_path(rel_file),
                keywords=extract_file_keywords(rel_file),
                domain=guess_domain(rel_file),
                description=description,
                severity="medium",
                confidence=0.45,
                occurrences=1,
                project_origin=project_name,
                resolution=f"Edge: {edge_id}. Reason: {reason[:100]}",
                related_files=[rel_file],
                scope="project",
            )
            store.record(entry)
    else:
        # Fallback: one generic entry scoped to source files
        entry_type = "smell_introduced" if smells_summary else "impact_high"
        description = f"{from_node} → {to_node}: {smells_summary[:120]}" if smells_summary else f"{from_node} → {to_node}"
        entry = ExperienceEntry(
            type=entry_type,
            file_pattern="src/**/*.ts",
            keywords=[w for w in f"{from_node} {to_node}".replace("-", " ").split() if len(w) > 2],
            domain="general",
            description=description,
            severity="medium",
            confidence=0.30,
            occurrences=1,
            project_origin=project_name,
            resolution=f"Edge: {edge_id}. Reason: {reason[:100]}",
            scope="project",
        )
        store.record(entry)

    store.save()


def _record_experience_fallback(result: dict, project_path: str,
                                 from_node: str, to_node: str, edge_id: str,
                                 reason: str, smells_summary: str) -> None:
    """Fallback when experience_memory module is unavailable."""
    project_name = Path(project_path).name
    mem_file = _xdg.project_memory_path(project_path)
    mem_file.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {"entries": []}
    if mem_file.exists():
        try:
            existing = json.loads(mem_file.read_text())
        except Exception:
            existing = {"entries": []}

    from datetime import datetime, timezone
    changed_files = _get_changed_files(project_path)
    file_pattern = "src/**/*.ts" if not changed_files else changed_files[0]

    entry = {
        "type": "smell_introduced" if smells_summary else "impact_high",
        "file_pattern": file_pattern,
        "keywords": [w for w in f"{from_node} {to_node}".replace("-", " ").split() if len(w) > 2],
        "domain": "general",
        "description": f"{from_node} → {to_node}: {smells_summary[:120]}" if smells_summary else f"{from_node} → {to_node}",
        "severity": "medium",
        "confidence": 0.30,
        "occurrences": 1,
        "project_origin": project_name,
        "resolution": f"Edge: {edge_id}. Reason: {reason[:100]}",
        "scope": "project",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "first_seen": datetime.now(timezone.utc).isoformat(),
    }

    entries: list = existing.get("entries", [])
    entries.append(entry)
    existing["entries"] = entries[-200:]

    try:
        mem_file.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
    except Exception:
        pass


def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        print(_APPROVE)
        return

    if "graph_traverse" not in hook_input.get("tool_name", ""):
        print(_APPROVE)
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        print(_APPROVE)
        return

    tool_result = hook_input.get("tool_result", {})
    if isinstance(tool_result, str):
        try:
            tool_result = json.loads(tool_result)
        except Exception:
            tool_result = {}

    from_node = tool_result.get("from_node", "")
    to_node = tool_result.get("to_node", "")

    try:
        _record_experience(tool_result, project_dir)
    except Exception:
        pass

    if from_node and to_node:
        print(f"⚡ {from_node} → {to_node} (experience recorded)", file=sys.stderr)

    try:
        _emit_usage_block(project_dir)
    except Exception:
        pass

    print(_APPROVE)


def _emit_usage_block(project_dir: str) -> None:
    """Print the usage block to stderr when a usage state file is present.

    vise does NOT write this file. It reads ``$VISE_USAGE_DIR/state.json``
    (documented path) so an external producer — a statusline script, another
    tool — can surface token/session usage at each phase transition. Absent
    file means no block, which is the normal case.

    Two dead paths were removed here: a ``_refresh_usage_state`` writer and a
    "try the installed package first" reader, both importing
    ``vise.engines.usage_state`` / ``usage_local``, which never shipped. The
    writer's ``except ImportError: return`` meant nothing ever populated the
    file from inside vise, and the reader always fell through to the inline
    formatter below. See test_no_phantom_imports.py.
    """
    block = _format_block(_read_state())
    if block:
        print(f"📊 {block}", file=sys.stderr)


def _read_state() -> dict:
    """Read the usage state JSON an external producer may have written."""
    path = _xdg.usage_dir() / "state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _format_block(state: dict) -> str:
    """Render `<used>/<total> | <pct>% usage reset at <t>` from usage state."""
    def _fmt_k(n: int) -> str:
        if n >= 1_000_000:
            v = n / 1_000_000
            return f"{v:.1f}M".replace(".0M", "M")
        if n >= 1_000:
            return f"{n / 1_000:.0f}k"
        return str(n)

    parts: list[str] = []
    used = state.get("context_used")
    total = state.get("context_total")
    if used is not None and total is not None:
        parts.append(f"{_fmt_k(int(used))}/{_fmt_k(int(total))}")
    if "session_pct" in state:
        s = f"{state['session_pct']}% usage"
        reset = state.get("session_reset")
        if reset:
            s += f" reset at {reset}"
        parts.append(s)
    return " | ".join(parts)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(_APPROVE)
