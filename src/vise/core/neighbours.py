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

#: `delta-cube` — the repo as points in a feature space, with a *Delta* per
#: reindex and a *Tension* wherever a change moved a file away from something
#: that imports it. vise used to carry this as a hard dependency and dropped
#: it; the `smell_*` and `tension_*` experience types are what remained. Only
#: the two calls vise's gate message names, not the server's 36.
#:
#: Tensions exist only after `cube_reindex` — an index nobody re-ran holds
#: zero of them, and zero reads as healthy. The gate says so rather than
#: counting them.
DELTA_CUBE_TOOLS: frozenset[str] = frozenset({
    "cube_index_directory",
    "cube_reindex",
})

#: `mempalace` — what was *said*: verbatim transcripts, searched by question.
#: The half of memory vise deliberately does not hold. vise's experience
#: memory is short lessons keyed to a file glob and injected on edit;
#: MemPalace is the record of what a session decided, tried and rejected, in
#: its own words. Only the two read calls vise teaches; the server has 45.
#:
#: `mempalace_search` takes `query` (keywords only, 250 chars — its own
#: schema says a pasted prompt sinks recall), optional `wing` (MemPalace's
#: word for a project; the repo's basename by its convention) and `limit`.
#: `mempalace_diary_read` takes `agent_name` and `last_n`. Nothing here
#: writes: MemPalace's own hooks save the transcript every fifteen messages,
#: and a second writer would file the same session twice.
MEMPALACE_TOOLS: frozenset[str] = frozenset({
    "mempalace_diary_read",
    "mempalace_search",
})

#: Every name vise assumes a neighbour exposes. What an asset may teach.
NEIGHBOUR_TOOLS: frozenset[str] = (
    LIVESPEC_TOOLS | LAYOUT_INSPECTOR_TOOLS | FLOWTRACE_TOOLS
    | DELTA_CUBE_TOOLS | MEMPALACE_TOOLS
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
    # 0.2.0 records a contract's baseline and a tension's current distance
    # with the same metric. Below it the baseline was Euclidean and the
    # current distance cosine, so `tension_percent` compared two scales and
    # the count a gate would read was not a number anyone should act on.
    "delta-cube": "0.2.0",
    # 3.3.0 is where MemPalace's Stop hook stopped making the agent write the
    # save in chat: below it, every fifteenth human message the hook returns
    # a `block` decision, which lands beside vise's own Stop gate and costs
    # the turn. From 3.3.0 the hook saves silently and the two coexist.
    "mempalace": "3.3.0",
}

#: Where delta-cube keeps its one database. Global, not per repo: every
#: project indexed on a machine shares it, keyed by absolute `file_path`, and
#: `$DCC_DATA_DIR` moves it. The default is jig's data dir because delta-cube
#: was extracted from jig and never changed it.
DELTA_CUBE_DATA_DIR_ENV = "DCC_DATA_DIR"
DELTA_CUBE_DEFAULT_DATA_DIR = "~/.local/share/jig"
DELTA_CUBE_DB_NAME = "dcc.db"

#: Not an MCP server vise teaches a single call of — MemPalace stores verbatim
#: transcripts and answers questions about them, which is the half of memory
#: vise deliberately does not hold. It earns a name here for the same reason
#: Graphify does: `mempalace init` writes two files into the repository root,
#: and `diff_scope` will fail on them unless its `allow` list knows.
MEMPALACE_PROJECT_FILES: tuple[str, ...] = ("mempalace.yaml", "entities.json")

#: Where MemPalace keeps its config and, under it, the palace. Resolved in
#: the order its own `config.py` uses: the env override, then a legacy
#: `~/.mempalace` that really holds an install, then XDG. The palace itself
#: is `palace/` under that dir unless `config.json` moves it.
#: What to say to someone who has no palace, and nothing more than that.
#:
#: vise does not install MemPalace and should not: a palace is machine-wide
#: and holds its owner's conversations, which is not a decision another
#: plugin's installer gets to make for them. Every other neighbour is offered
#: the same way — named, never installed — and MemPalace is the one whose
#: absence is otherwise invisible, because a repo that has never seen it looks
#: exactly like a repo whose owner declined it.
MEMPALACE_ABSENT_HINT: tuple[str, ...] = (
    "no palace on this machine — earlier sessions are not searchable.",
    "MemPalace mines Claude Code transcripts (which expire after 30 days) and",
    "searches them verbatim: `uv tool install mempalace`, then `mempalace init <repo>`.",
)

MEMPALACE_CONFIG_DIR_ENV = "MEMPALACE_CONFIG_DIR"
MEMPALACE_LEGACY_DIR = "~/.mempalace"
MEMPALACE_XDG_SUBDIR = "mempalace"
MEMPALACE_PALACE_SUBDIR = "palace"
MEMPALACE_PALACE_MARKER = "chroma.sqlite3"

#: Not an MCP server vise talks to at all — a CLI that writes a file livespec
#: reads. It earns a name here because its presence changes what vise's own
#: gates see: `graphify-out/` is committed by convention and rebuilt by a git
#: post-commit hook, so a repo that uses it has files in every diff that
#: nobody edited.
GRAPHIFY_OUT_DIR = "graphify-out"
GRAPHIFY_GRAPH = "graphify-out/graph.json"
