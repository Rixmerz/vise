"""``vise neighbours`` — what the servers vise runs beside left on disk.

vise cannot call livespec, flowtrace or layout-inspector, so for a long time
the honest answer to "is the symbol layer available here?" was "ask the agent".
Two of the three leave artifacts in the repository, and this reports them —
the same facts the `symbol_index` and `trace_captured` gates read, so a person
debugging a refusal sees exactly what refused.

layout-inspector deliberately has no row: it leaves nothing behind, and
inventing a "probably mounted" line would be the guess this command exists to
replace. What it *can* report is whether vise's own render gates could run,
which is the neighbouring question and is answerable.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from vise.cli._browser_probe import browser_status_quiet
from vise.core.neighbours import MINIMUM_VERSIONS


def _render_gates_line(project: Path) -> str:
    """Whether vise's own browser gates could run here. Not a neighbour, but
    the question people are really asking when they ask about the other one."""
    try:
        from vise.engines.design_profile import load_design_config

        available, reason = browser_status_quiet()
        if not available:
            return f"unavailable — {reason}"
        config = load_design_config(project)
        if not config.targets:
            return (
                "browser ready, but `design.targets` in .vise/quality.yaml is "
                "empty — the render gates fail closed with nothing to render"
            )
        return f"ready — {len(config.targets)} target(s), {len(config.breakpoints)} breakpoints"
    except Exception as exc:  # noqa: BLE001 - a report must not raise
        return f"could not check: {type(exc).__name__}: {exc}"


def _cmd_neighbours(args: argparse.Namespace) -> int:
    from vise.core.neighbour_state import graph_state, index_state, trace_state

    project = Path(args.project_dir or ".").expanduser().resolve()
    print(f"{project}\n")

    index = index_state(project)
    print(f"  livespec          {index.detail}")
    trace = trace_state(project)
    print(f"  flowtrace         {trace.detail}")
    graph = graph_state(project)
    print(f"  Graphify          {graph.detail}")
    print(f"  vise render gates {_render_gates_line(project)}")

    print("\nminimum versions vise's guidance assumes:")
    for name, version in sorted(MINIMUM_VERSIONS.items()):
        print(f"  {name:<18} {version}")

    if index.refuses:
        print(
            "\nWithout an index, `symbol_index` fails closed and the CodeLayer "
            "gate stands down — redirecting a read to a tool that cannot "
            "answer is a wall, not a gate."
        )
    if graph.freshness == "differs":
        print(
            "\nThe ingested edges came from a graph that is no longer on disk. "
            "Caller counts include code that has moved until it is re-ingested."
        )
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "neighbours",
        help="what livespec, flowtrace and Graphify left in this repo",
    )
    p.add_argument("--project-dir", default=None, help="defaults to the cwd")
    p.set_defaults(func=_cmd_neighbours)
