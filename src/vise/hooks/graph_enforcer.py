#!/usr/bin/env python3
"""Graph Enforcer Hook — hard-blocks tools listed in current node's tools_blocked.

PreToolUse hook for Claude Code. Reads the active graph workflow state
(graph_state.json) and graph definition (graph.yaml) to enforce tool
restrictions per node. Fail-safe: approves on any error.

Replaces legacy workflow_enforcer.py which read steps.yaml/state.json.
"""

import json
import sys
import os
from pathlib import Path

try:
    from vise.hooks import _xdg
except Exception:
    # PYTHONPATH misconfiguration or non-standard deployment: fall back to
    # an inline stdlib-only mirror so this hard-blocking gate still fails
    # open instead of crashing before it can print a decision.
    class _xdg:  # type: ignore[no-redef]
        @staticmethod
        def data_dir() -> Path:
            raw = os.environ.get("XDG_DATA_HOME")
            base = Path(raw) if raw and Path(raw).is_absolute() else Path.home() / ".local" / "share"
            return base / "vise"


#: Block-scalar introducers. Everything indented under one of these is prose,
#: not structure, and must not be read as either.
_BLOCK_SCALARS = frozenset({"|", ">", "|-", ">-", "|+", ">+", "|2", ">2"})


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _scalar(raw):
    return raw.strip().strip('"').strip("'")


def _flow_items(raw):
    """Items of an inline sequence: ``["A", "B"]`` -> ``["A", "B"]``.

    The real parser accepts this form and so must this one — a node written
    ``tools_blocked: ["Bash"]`` used to parse as empty, which is a gate its
    author declared, that vise accepted, and that blocked nothing.
    """
    inner = raw.strip()
    if inner.startswith("["):
        inner = inner[1:]
    if inner.endswith("]"):
        inner = inner[:-1]
    return [_scalar(part) for part in inner.split(",") if part.strip()]


def parse_tools_blocked(content):
    """Extract node_id -> tools_blocked mapping from graph YAML.

    Deliberately partial: it answers one question — which tools does this
    node block — and skips everything else rather than guessing at it. And
    deliberately stdlib-only, because this runs before *every* tool call.
    Measured rather than assumed: adding ``import yaml`` takes the hook's
    median startup from 25 ms to 48 ms with a half-second tail, which is not
    a trade a gate on the hot path gets to make.

    But a light parser still has to be a correct one, and this one used to be
    structurally blind — it matched ``- id:`` and ``tools_blocked:`` at any
    depth, which broke three ways, each of them silent and each failing OPEN:

    * a ``dag`` node whose ``tasks:`` came before its ``tools_blocked:`` had
      the block list attributed to the last task id, so the node blocked
      nothing;
    * the inline form above parsed as empty;
    * a line of prose inside ``prompt_injection: |`` beginning ``- id:``
      invented a node and took the real node's restrictions with it.

    Indentation is the whole fix: a ``- id:`` deeper than the node sequence
    is a task, a key that is not at the node's own key indent belongs to
    something nested, and a block scalar is consumed without being read.

    ``test_graph_enforcer_parser.py`` pins the result against
    ``graph_parser.load_graph_from_file`` over every bundled workflow. That
    comparison is what found all three.
    """
    mapping = {}
    lines = content.splitlines()

    # Only the top-level `nodes:` section describes nodes. Starting anywhere
    # is how a `metadata:` block or a recipe file could contribute entries.
    start = None
    for index, line in enumerate(lines):
        if _indent(line) == 0 and line.strip().startswith("nodes:"):
            start = index + 1
            break
    if start is None:
        return mapping

    node_indent = None   # column of the "-" that opens a node
    key_indent = None    # column of the keys belonging to the current node
    node_id = None
    collecting = False   # inside this node's tools_blocked block sequence

    i = start
    total = len(lines)
    while i < total:
        line = lines[i]
        stripped = line.strip()
        i += 1

        if not stripped or stripped.startswith("#"):
            continue

        indent = _indent(line)

        if stripped.startswith("- "):
            if node_indent is None:
                node_indent = indent  # the first item fixes the node depth
            if indent < node_indent:
                break  # dedented out of the nodes section
            if indent > node_indent:
                # Deeper item: a tools_blocked entry if we are collecting one,
                # otherwise a nested task — which is not a node.
                if collecting and node_id is not None:
                    mapping[node_id].append(_scalar(stripped[2:]))
                continue
            collecting = False
            if stripped.startswith("- id:"):
                node_id = _scalar(stripped.split(":", 1)[1])
                mapping.setdefault(node_id, [])
                # Sibling keys align with `id`, wherever the dash put it.
                after_dash = line[indent + 1:]
                key_indent = indent + 1 + (len(after_dash) - len(after_dash.lstrip(" ")))
            else:
                node_id = None
                key_indent = None
            continue

        if indent == 0:
            break  # the next top-level key ends the nodes section

        if node_id is None or indent != key_indent or ":" not in stripped:
            # Belongs to something nested (a task's own fields), or is not a
            # key at all. Either way it ends any list we were collecting.
            collecting = False
            continue

        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest in _BLOCK_SCALARS:
            collecting = False
            while i < total:
                nxt = lines[i]
                if nxt.strip() and _indent(nxt) <= indent:
                    break
                i += 1
            continue

        if key != "tools_blocked":
            collecting = False
            continue

        if rest.startswith("["):
            mapping[node_id] = _flow_items(rest)
            collecting = False
        elif rest:
            mapping[node_id] = [_scalar(rest)]
            collecting = False
        else:
            mapping[node_id] = []
            collecting = True

    return mapping


def get_state_path(project_dir):
    """Resolve graph_state.json path.

    This hook is a hard-blocking PreToolUse gatekeeper, so path resolution
    goes through ``vise.hooks._xdg`` (stdlib-only, with an inline fallback
    above if even that import fails) — never the heavier ``vise.core``
    package. That module is the single source of truth for the XDG data
    dir, shared with ``vise.core.state_paths.graph_state_path()``.

    This implementation additionally checks the legacy ``config.json``
    hub-dir override for installs that moved their hub before XDG was
    adopted, and falls back to the project-local
    ``.claude/workflow/graph_state.json`` for pre-XDG manual setups.
    """
    project_name = Path(project_dir).name
    # Delegate to the shared resolver rather than rebuilding the path here:
    # it owns the collision-proof key for two projects sharing a basename,
    # and a gate that computes its own path is exactly how this hook and the
    # MCP tools ended up reading two different state trees. The inline
    # fallback class above mirrors only data_dir(), so degrade to the plain
    # basename form when the real module could not be imported.
    resolver = getattr(_xdg, "graph_state_path", None)
    if resolver is not None:
        xdg_state = resolver(project_dir)
    else:
        xdg_state = _xdg.data_dir() / "states" / project_name / "graph_state.json"
    if xdg_state.exists():
        return xdg_state

    # Legacy hub override via explicit config.json (still honoured if present)
    config_file = _xdg.data_dir() / "config.json"
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
            hub_dir = config.get("hub_dir")
            if hub_dir:
                states_dir = config.get("states_dir", "states")
                override = Path(hub_dir) / states_dir / project_name / "graph_state.json"
                if override.exists():
                    return override
        except Exception:
            pass

    # Project-local fallback
    return Path(project_dir) / ".claude" / "workflow" / "graph_state.json"


# Tools that must ALWAYS pass the enforcer, no matter what the active
# graph YAML says. These are the recovery and inspection tools — if they
# could be blocked, a misconfigured workflow would lock the user out
# with no in-band way to disable the enforcer or reset state.
#
# Keep this list short and explicit. Read-only inspection + the enforcer
# toggle + graph reset. Nothing that mutates code or runs shell.
#
# Matched by SUFFIX (see _tool_suffix), not full prefixed name: the MCP
# namespace prefix depends on how the server is registered
# ("mcp__vise__x" under a manual MCP install, "mcp__plugin_vise_vise__x"
# under the Claude Code plugin, and potentially something else after a
# future rename). Matching only the trailing tool name after the last
# "__" keeps this allowlist correct across all of those.
GRAPH_INNER_ALLOWLIST = frozenset({
    "graph_enforcer_toggle",
    "graph_status",
    # graph_reset is NOT an exit — it returns to the start node, which is the
    # most restrictive node in every bundled workflow. graph_deactivate is the
    # real one; graph_set_node is the manual override. Both must stay callable
    # or a stale workflow has no in-band way out.
    "graph_reset",
    "graph_deactivate",
    "graph_set_node",
    "graph_list_available",
    "graph_timeline",
})

# Safe regardless of how the tool call arrives: directly (current plugin
# model) or wrapped in an execute_mcp_tool-style proxy call.
ENFORCER_ALLOWLIST = GRAPH_INNER_ALLOWLIST | frozenset({
    "vise_guide",
    "vise_version",
})

_EXECUTE_MCP_TOOL_SUFFIX = "execute_mcp_tool"


def _tool_suffix(tool_name):
    """Return the trailing segment of a namespaced MCP tool name.

    "mcp__plugin_vise_vise__graph_status" -> "graph_status"
    "mcp__vise__graph_status"             -> "graph_status"
    "graph_status"                        -> "graph_status" (already bare)
    """
    if "__" in tool_name:
        return tool_name.rsplit("__", 1)[-1]
    return tool_name


def main():
    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        print(json.dumps({"decision": "approve"}))
        return

    tool_name = hook_input.get("tool_name", "")
    suffix = _tool_suffix(tool_name)

    # Hardcoded escape hatch: control + read-only graph tools always pass.
    # This is the in-band recovery path — without it, a stuck workflow
    # has no way back without editing files from a separate terminal.
    # Matched by suffix so it works whether the MCP is registered as
    # "mcp__vise__x" or "mcp__plugin_vise_vise__x" (see _tool_suffix).
    if suffix in ENFORCER_ALLOWLIST:
        print(json.dumps({"decision": "approve"}))
        return

    if suffix == _EXECUTE_MCP_TOOL_SUFFIX:
        # Proxied call: the real tool name is nested in tool_input.
        inner = hook_input.get("tool_input", {}).get("tool_name", "")
        if _tool_suffix(inner) in GRAPH_INNER_ALLOWLIST:
            print(json.dumps({"decision": "approve"}))
            return
        # Fall through — inner tool subject to normal blocking below

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        print(json.dumps({"decision": "approve"}))
        return

    try:
        # 1. Read graph state (centralized hub or local)
        state_path = get_state_path(project_dir)
        if not state_path.exists():
            print(json.dumps({"decision": "approve"}))
            return

        state = json.loads(state_path.read_text())
        active_graph = state.get("active_graph")
        current_nodes = state.get("current_nodes", [])

        if not active_graph or not current_nodes:
            print(json.dumps({"decision": "approve"}))
            return

        # Check enforcer_enabled flag (written by the UI toggle)
        config_path = state_path.parent / "config.json"
        if config_path.exists():
            cfg = json.loads(config_path.read_text())
            if not cfg.get("enforcer_enabled", True):
                print(json.dumps({"decision": "approve"}))
                return

        current_node = current_nodes[0]

        # 2. Read graph YAML (always local to project)
        graph_file = Path(project_dir) / ".claude" / "workflow" / "graph.yaml"
        if not graph_file.exists():
            print(json.dumps({"decision": "approve"}))
            return

        blocked_map = parse_tools_blocked(graph_file.read_text())
        tools_blocked = blocked_map.get(current_node, [])

        # For execute_mcp_tool, check the inner tool name against blocked list
        effective = tool_name
        if suffix == _EXECUTE_MCP_TOOL_SUFFIX:
            effective = hook_input.get("tool_input", {}).get("tool_name", tool_name)

        # 3. Check if tool is blocked ("*" = block everything)
        if "*" in tools_blocked or effective in tools_blocked:
            reason = (
                f"[Graph Enforcer] Tool '{effective}' is blocked at node "
                f"'{current_node}' (workflow: {active_graph}). "
                f"Advance the workflow with graph_traverse to use this tool, "
                f"or graph_deactivate to end the workflow if it no longer "
                f"describes the work. "
                f"If the MCP server is unreachable and you cannot call "
                f"either, run `vise graph reset --project "
                f"{project_dir}` from a terminal to clear the state."
            )
            # Two channels on purpose.
            #
            # `hookSpecificOutput.permissionDecisionReason` is the documented
            # PreToolUse shape and the ONLY one whose text reaches the agent:
            # with `decision`/`message` alone the call was still denied, but the
            # agent saw a bare "Hook PreToolUse:Edit denied this tool" and was
            # told nothing about which phase blocked it or how to advance.
            # Verified twice — main agent and a dispatched subagent both got the
            # generic string. A gate that blocks without saying why teaches the
            # agent to flail or route around it.
            #
            # `decision: block` stays because it is what empirically blocks on
            # the installed Claude Code today, and blocking is the single load-
            # bearing behaviour of this hook. Dropping it on the strength of a
            # docs reading — while the live evidence says it works — would risk
            # silently disarming every gate vise has. Remove it only after
            # confirming a deny still lands without it.
            print(json.dumps({
                "decision": "block",
                "message": reason,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
            }))
            return

    except Exception as exc:
        # Fail-safe: approve on any error — a crashing enforcer must never brick
        # a session. But approve SILENTLY and enforcement can die (corrupt state
        # file, parser bug) while the user keeps believing a workflow is gating
        # their tools. Same contract as lint_pass / lsp_clean: fail open, say why.
        # stderr only — stdout is the decision channel and must stay pure JSON.
        print(
            f"[vise.enforcer] approving without gating — enforcer error: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
