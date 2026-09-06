"""The names vise assumes another server exposes.

vise's MCP server runs *beside* other servers in a session, not above it. It
cannot call them — MCP has no server-to-server channel — and yet it names their
tools in thirty-odd places: the ``codelayer_gate`` deny message, two skills,
three commands, two workflows, the README. None of those places can check that
the name still exists, because those servers are not in this repository.

What *can* be checked is that vise agrees with itself. These sets are the one
place the assumption is written down; ``test_neighbour_contract.py`` holds every
asset to them. A rename on a neighbour's side still has to be discovered by a
person — but it is then one edit here, and the suite names every other line to
fix.

Only names vise actually says are pinned. A pin nothing references is a pin
that outlived its reason, and the suite fails on those too — so a neighbour's
full tool surface does not belong here, only the part vise teaches.

Discovered the hard way, 2026-09-06
-----------------------------------
This module used to pin ``locate`` and ``compute_index_status``. Neither is a
livespec tool. ``locate`` never existed — the orientation call is
``find_symbol`` — and ``compute_index_status`` was removed as a tool in
livespec v0.9; it survives as a module-level helper behind the
``project://index/status`` *resource*, which is not something a deny message
can tell an agent to call. Both names shipped in the ``codelayer_gate`` deny
message and in the decouple workflow's first step, which means the two places
vise most depends on being obeyed were naming calls that fail.

The contract test did exactly what its docstring promised — it kept vise
consistent with itself — and consistency with itself is not correctness. That
is why ``MINIMUM_VERSIONS`` exists now: a version is the one fact about a
neighbour that a person can check in a minute, and the one this file could
have carried the whole time.
"""
from __future__ import annotations

#: `livespec` — the repo as a symbol graph. Read by symbol instead of by file
#: and you get the body plus the signatures of what it calls, in one call.
#:
#: Every one of these takes a REQUIRED ``workspace`` argument (the absolute
#: repo root). livespec removed its environment fallback, so an example that
#: omits it teaches a call that raises.
LIVESPEC_TOOLS: frozenset[str] = frozenset({
    "analyze_impact",
    "debt_baseline_capture",
    "debt_baseline_status",
    "find_symbol",
    "git_diff_impact",
    "index_project",
    "ingest_external_graph",
    "quick_orient",
    "read_unit",
    "resolve_location",
    "search_similar",
    "who_calls",
})

#: `layout-inspector` — rendered geometry, measured rather than looked at. The
#: other half of vise's own render gates: `ui_layout` says a page fails, these
#: say why. Only the ones vise teaches; the server exposes more.
LAYOUT_INSPECTOR_TOOLS: frozenset[str] = frozenset({
    "accessibility_spatial",
    "check_environment",
    "compare_viewports",
    "detect_issues",
    "element_context",
})

#: `flowtrace` — what the program actually did, as paired enter/exit events in
#: a JSONL file. The runtime half of the picture livespec draws statically.
#: These read a trace; producing one is the `flowtrace run` CLI, not a tool.
FLOWTRACE_TOOLS: frozenset[str] = frozenset({
    "log_aggregate",
    "log_open",
    "trace_diff",
    "trace_find_error",
    "trace_tree",
})

#: Every name vise assumes a neighbour exposes. What an asset may teach.
NEIGHBOUR_TOOLS: frozenset[str] = (
    LIVESPEC_TOOLS | LAYOUT_INSPECTOR_TOOLS | FLOWTRACE_TOOLS
)

#: The oldest release of each neighbour in which every name above resolves and
#: means what vise says it means. Not a guess at what is installed — vise
#: cannot see that — but the number a person checks when a taught call fails.
#:
#: Each entry says what breaks below it, because "upgrade" without a reason is
#: advice nobody acts on:
MINIMUM_VERSIONS: dict[str, str] = {
    # 0.31 is livespec's hard cut to OpenSpec slugs, and the release by which
    # `workspace` is required on every call with no environment fallback.
    "livespec": "0.31",
    # 0.4.0 is the correctness release: before it every detector had a false
    # positive a real page hits, `compare_viewports` returned bare counts
    # rather than a diff, and `check_environment` did not exist.
    "layout-inspector": "0.4.0",
    # Plugin 2.7.0 / CLI 4.0.0. Below it a traced Python run exits 0 whatever
    # the program did and skips `atexit`, so a crash reads as a pass and a
    # coverage run loses its data — both of which would corrupt a gate that
    # reads the trace.
    "flowtrace": "2.7.0",
}

#: Not an MCP server vise talks to at all — a CLI that writes a file livespec
#: reads. It earns a name here because its presence changes what vise's own
#: gates see: `graphify-out/` is committed by convention and rebuilt by a git
#: post-commit hook, so a repo that uses it has files in every diff that
#: nobody edited.
GRAPHIFY_OUT_DIR = "graphify-out"
GRAPHIFY_GRAPH = "graphify-out/graph.json"
