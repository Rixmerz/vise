"""The names vise assumes another server exposes.

``codelayer_gate`` denies a ``Read`` on source and answers with the call to
make instead — ``read_unit(...)``, ``locate(...)``. Those tools are livespec's,
a separate MCP that is not part of vise, and vise names them in twenty-odd
places: the deny message, two skills, three commands, the README. None of
those places can check that the name still exists, because livespec is not in
this repository.

What *can* be checked is that vise agrees with itself. This set is the one
place the assumption is written down; ``test_livespec_contract.py`` holds every
asset to it. A rename on livespec's side still has to be discovered by a person
— but it is then one edit here, and the suite names every other line to fix.

No livespec version is pinned because none is known to be required. When one
is, it belongs here, next to the names.
"""
from __future__ import annotations

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
