"""vise runtime — read the plan before authorising the run.

Two subcommands, both read-only and both offline:

``vise runtime plan``   what a DAG node would dispatch: the waves, who runs each
                        task, on which model, why, and what the whole thing is
                        estimated to cost.
``vise runtime agents`` what the registry can route to, and which roles have an
                        ambiguity that would make a task unroutable.

Neither executes anything. That is the point — a plan nobody can read is a plan
nobody can refuse, and the moment to refuse a four-dollar run over a two-line
change is before it starts. Dispatch lands in a later milestone; see
docs/scheduler.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_node_tasks(graph_path: Path, node_id: str | None):
    """Return (node_id, tasks) for the DAG node to plan, or exit with a message."""
    from vise.engines.graph_parser import GraphParseError, load_graph_from_file

    try:
        graph = load_graph_from_file(graph_path)
    except (OSError, GraphParseError) as exc:
        print(f"vise runtime: cannot read {graph_path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    dag_nodes = [n for n in graph.nodes.values() if n.node_type == "dag" and n.tasks]
    if node_id:
        node = graph.nodes.get(node_id)
        if node is None:
            print(f"vise runtime: no node {node_id!r} in {graph_path.name}", file=sys.stderr)
            raise SystemExit(2)
        if node.node_type != "dag" or not node.tasks:
            print(
                f"vise runtime: node {node_id!r} is node_type={node.node_type!r} with "
                f"{len(node.tasks)} task(s) — only a dag node with tasks has anything "
                f"to schedule",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return node.id, node.tasks
    if not dag_nodes:
        print(
            f"vise runtime: {graph_path.name} has no dag node with tasks — nothing to plan",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if len(dag_nodes) > 1:
        names = ", ".join(n.id for n in dag_nodes)
        print(
            f"vise runtime: {graph_path.name} has several dag nodes ({names}); "
            f"pass --node to pick one",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return dag_nodes[0].id, dag_nodes[0].tasks


def _cmd_plan(args: argparse.Namespace) -> int:
    from vise.runtime.contracts import RunBudget
    from vise.runtime.planner import plan

    node_id, tasks = _load_node_tasks(Path(args.graph), args.node)
    budget = RunBudget(
        max_cost_usd=args.max_cost or 0.0,
        max_parallel=args.max_parallel,
    )
    result = plan(tasks, budget=budget, completed=args.completed or ())

    if args.json:
        payload = result.to_dict()
        payload["node"] = node_id
        print(json.dumps(payload, indent=2))
    else:
        print(f"node: {node_id}  ({Path(args.graph).name})\n")
        print(result.render())

    # Non-zero on an unplannable node. A plan with problems that exits 0 is a
    # plan a script will happily act on.
    return 1 if result.problems else 0


def _cmd_agents(args: argparse.Namespace) -> int:
    from vise.runtime.registry import AgentRegistry

    registry = (
        AgentRegistry.from_dir(Path(args.dir)) if args.dir else AgentRegistry.bundled()
    )
    if not registry.agents:
        print("vise runtime: no agents found — not running from a plugin checkout?",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            {"agents": [a.to_dict() for a in registry.agents.values()]}, indent=2
        ))
        return 0

    print(f"{'agent':<22} {'role':<10} {'writes':<7} {'model':<8} capabilities")
    for agent in sorted(registry.agents.values(), key=lambda a: (a.role or "~", a.id)):
        print(
            f"{agent.id:<22} {(agent.role or '—'):<10} "
            f"{str(agent.writes).lower():<7} {(agent.model or '—'):<8} "
            f"{', '.join(agent.capabilities)}"
        )

    ambiguous = [
        role for role in sorted(registry.roles())
        if registry.resolve(role).agent is None
    ]
    if ambiguous:
        print(
            "\nroles a task cannot reach without naming a capability: "
            + ", ".join(ambiguous)
        )
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("runtime", help="plan a DAG node's work (read-only, offline)")
    inner = p.add_subparsers(dest="runtime_command")

    plan_p = inner.add_parser("plan", help="waves, agents, models and estimated cost")
    plan_p.add_argument("graph", help="path to a *-graph.yaml")
    plan_p.add_argument("--node", default=None, help="which dag node to plan")
    plan_p.add_argument("--max-cost", type=float, default=None,
                        help="run cost ceiling in USD; tasks that do not fit are reported")
    plan_p.add_argument("--max-parallel", type=int, default=4,
                        help="how many tasks may run at once (default 4)")
    plan_p.add_argument("--completed", nargs="*", default=None,
                        help="task ids already done; they are not replanned")
    plan_p.add_argument("--json", action="store_true", help="machine-readable output")
    plan_p.set_defaults(func=_cmd_plan)

    agents_p = inner.add_parser("agents", help="what the registry can route to")
    agents_p.add_argument("--dir", default=None, help="read charters from this directory")
    agents_p.add_argument("--json", action="store_true", help="machine-readable output")
    agents_p.set_defaults(func=_cmd_agents)
