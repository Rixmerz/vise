"""The names vise assumes another server exposes.

vise's MCP server runs *beside* other servers in a session, not above it. It
cannot call them — MCP has no server-to-server channel — and yet it names their
tools in thirty-odd places: the ``codelayer_gate`` deny message, two skills,
three commands, a workflow, the README. None of those places can check that the
name still exists, because those servers are not in this repository.

What *can* be checked is that vise agrees with itself. These sets are the one
place the assumption is written down; ``test_neighbour_contract.py`` holds every
asset to them. A rename on a neighbour's side still has to be discovered by a
person — but it is then one edit here, and the suite names every other line to
fix.

Only names vise actually says are pinned. A pin nothing references is a pin
that outlived its reason, and the suite fails on those too — so a neighbour's
full tool surface does not belong here, only the part vise teaches.

No version is pinned for either, because none is known to be required. When one
is, it belongs here, next to the names.
"""
from __future__ import annotations

#: `livespec` — the repo as a symbol graph. Read by symbol instead of by file
#: and you get the body plus the signatures of what it calls, in one call.
LIVESPEC_TOOLS: frozenset[str] = frozenset({
    "analyze_impact",
    "compute_index_status",
    "debt_baseline_capture",
    "debt_baseline_status",
    "index_project",
    "locate",
    "read_unit",
    "resolve_location",
    "search_similar",
    "who_calls",
})

#: `layout-inspector` — rendered geometry, measured rather than looked at. The
#: other half of vise's own render gates: `ui_layout` says a page fails, these
#: say why. Only the three vise teaches; the server exposes more.
LAYOUT_INSPECTOR_TOOLS: frozenset[str] = frozenset({
    "compare_viewports",
    "detect_issues",
    "element_context",
})

#: Every name vise assumes a neighbour exposes. What an asset may teach.
NEIGHBOUR_TOOLS: frozenset[str] = LIVESPEC_TOOLS | LAYOUT_INSPECTOR_TOOLS
