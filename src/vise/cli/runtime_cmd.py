"""vise runtime — read the plan before authorising the run.

Seven subcommands. Six are read-only and offline; exactly one spends money, and
it will not do so without being told twice.

``plan``    what a DAG node would dispatch: the waves, who runs each task, on
            which model, why, and what the whole thing is estimated to cost.
``agents``  what the registry can route to, and which roles are ambiguous.
``run``     actually dispatch. Prints the plan and its cost and stops there
            unless ``--yes`` is given — a plan nobody can read is a plan nobody
            can refuse, and the moment to refuse a four-dollar run over a
            two-line change is before it starts.
``status``  where runs stand, from their state files.
``explain`` why a run did what it did: every scheduler decision, in order.
``budget``  what a run cost, per task.
``cancel``  ask a running scheduler to stop, from another terminal.
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
    result = plan(
        tasks, budget=budget, completed=args.completed or (),
        project_dir=str(Path(args.project_dir).resolve()),
        change=args.change or "",
    )

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
        AgentRegistry.from_dir(Path(args.dir)) if args.dir
        else AgentRegistry.for_project(Path(args.project_dir).resolve())
    )
    if not registry.agents:
        print("vise runtime: no agents found — not running from a plugin checkout?",
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            {
                "agents": [
                    {**a.to_dict(), "origin": registry.origins.get(a.id, "bundled")}
                    for a in registry.agents.values()
                ],
                "shadowed": list(registry.shadowed),
                "refused": [{"path": p, "reason": r} for p, r in registry.refused],
            },
            indent=2
        ))
        return 0

    print(f"{'agent':<22} {'from':<8} {'role':<10} {'writes':<7} {'model':<8} capabilities")
    for agent in sorted(registry.agents.values(), key=lambda a: (a.role or "~", a.id)):
        origin = registry.origins.get(agent.id, "bundled")
        mark = "*" if agent.id in registry.shadowed else ""
        print(
            f"{agent.id + mark:<22} {origin:<8} {(agent.role or '—'):<10} "
            f"{str(agent.writes).lower():<7} {(agent.model or '—'):<8} "
            f"{', '.join(agent.capabilities)}"
        )
    if registry.shadowed:
        print(f"\n* replaces a bundled agent: {', '.join(sorted(registry.shadowed))}")
    for path, reason in registry.refused:
        print(f"refused {path}: {reason}", file=sys.stderr)

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
    plan_p.add_argument("--project-dir", default=".",
                        help="the working tree the plan is checked against")
    plan_p.add_argument("--change", default=None,
                        help="the openspec change this run implements; "
                             "without it any well-formed active change satisfies the gate")
    plan_p.add_argument("--json", action="store_true", help="machine-readable output")
    plan_p.set_defaults(func=_cmd_plan)

    agents_p = inner.add_parser("agents", help="what the registry can route to")
    agents_p.add_argument("--dir", default=None, help="read charters from this directory")
    agents_p.add_argument("--project-dir", default=".",
                          help="the project whose .vise/agents/ is layered on the fleet")
    agents_p.add_argument("--json", action="store_true", help="machine-readable output")
    agents_p.set_defaults(func=_cmd_agents)

    run_p = inner.add_parser("run", help="dispatch a DAG node's tasks (spends money)")
    run_p.add_argument("graph", help="path to a *-graph.yaml")
    run_p.add_argument("--node", default=None, help="which dag node to run")
    run_p.add_argument("--goal", default=None, help="what this run is for")
    run_p.add_argument("--project-dir", default=".", help="the working tree to run against")
    run_p.add_argument("--change", default=None,
                       help="the openspec change this run implements; "
                            "without it any well-formed active change satisfies the gate")
    run_p.add_argument("--max-cost", type=float, default=None, help="run cost ceiling in USD")
    run_p.add_argument("--max-parallel", type=int, default=4)
    run_p.add_argument("--max-workers", type=int, default=None)
    run_p.add_argument("--max-wall-time", type=float, default=None)
    run_p.add_argument("--permission-mode", default=None,
                       help="passed to the CLI; omitted by default so the run "
                            "does not widen permissions on its own")
    run_p.add_argument("--isolate", action="store_true",
                       help="give each writing task its own git worktree and "
                            "integrate only after it verifies; needs a git repo "
                            "with a commit, and degrades to the shared tree with "
                            "a reason on the record if it cannot")
    run_p.add_argument("--no-verify", action="store_true",
                       help="skip the second-opinion pass (cheaper, and a worker "
                            "then grades its own homework)")
    run_p.add_argument("--run-id", default=None)
    run_p.add_argument("--state-dir", default=None)
    run_p.add_argument("--yes", action="store_true",
                       help="actually dispatch; without it the plan is printed and nothing runs")
    run_p.set_defaults(func=_cmd_run)

    resume_p = inner.add_parser("resume", help="continue a run that stopped")
    resume_p.add_argument("run_id")
    resume_p.add_argument("--graph", default=None,
                          help="path to the graph, if it has moved since the run")
    resume_p.add_argument("--project-dir", default=None,
                          help="defaults to the project the run was against")
    resume_p.add_argument("--permission-mode", default=None)
    # The run's SchedulerConfig is not part of RunSpec, so it is not in the
    # state file and cannot be recovered. Restate it rather than silently
    # resuming an isolated run in the shared tree.
    resume_p.add_argument("--isolate", action="store_true",
                          help="as for `run`; NOT remembered from the original run")
    resume_p.add_argument("--no-verify", action="store_true",
                          help="as for `run`; NOT remembered from the original run")
    resume_p.add_argument("--change", default=None,
                          help="as for `run`; NOT remembered from the original run")
    resume_p.add_argument("--state-dir", default=None)
    resume_p.add_argument("--yes", action="store_true",
                          help="actually dispatch; without it the remaining work is printed")
    resume_p.set_defaults(func=_cmd_resume)

    continue_p = inner.add_parser(
        "continue", help="run a composed plan as the continuation of a stopped run")
    continue_p.add_argument("run_id", help="the run this one continues")
    continue_p.add_argument("--graph", required=True,
                            help="the composed graph to run")
    continue_p.add_argument("--node", default=None,
                            help="which dag node of the composed graph")
    continue_p.add_argument("--goal", default=None,
                            help="defaults to the goal of the run being continued")
    continue_p.add_argument("--max-cost", type=float, default=0.0,
                            help="ceiling for the CHAIN; the prior run's spend counts")
    continue_p.add_argument("--max-parallel", type=int, default=4)
    continue_p.add_argument("--skip-done", action="store_true",
                            help="treat the prior run's successes as done, even "
                                 "where this plan declares them again")
    continue_p.add_argument("--project-dir", default=None,
                            help="defaults to the project the prior run was against")
    continue_p.add_argument("--permission-mode", default=None)
    continue_p.add_argument("--isolate", action="store_true")
    continue_p.add_argument("--no-verify", action="store_true")
    continue_p.add_argument("--change", default=None)
    continue_p.add_argument("--state-dir", default=None)
    # NOT `--run-id`: the positional is already `run_id` (the run being
    # continued) and argparse would have the option quietly overwrite it.
    continue_p.add_argument("--new-run-id", dest="new_run_id", default=None,
                            help="id for the continuation; generated when omitted")
    continue_p.add_argument("--yes", action="store_true",
                            help="actually dispatch; without it the plan is printed")
    continue_p.set_defaults(func=_cmd_continue)

    compose_p = inner.add_parser(
        "compose", help="what a finished run says about the plan that should follow it")
    compose_p.add_argument("run_id")
    compose_p.add_argument("--state-dir", default=None)
    compose_p.add_argument("--json", action="store_true")
    compose_p.set_defaults(func=_cmd_compose)

    status_p = inner.add_parser("status", help="where runs stand")
    status_p.add_argument("run_id", nargs="?", default=None)
    status_p.add_argument("--limit", type=int, default=5)
    status_p.add_argument("--state-dir", default=None)
    status_p.set_defaults(func=_cmd_status)

    explain_p = inner.add_parser("explain", help="why a run did what it did")
    explain_p.add_argument("run_id")
    explain_p.add_argument("--state-dir", default=None)
    explain_p.add_argument("--json", action="store_true")
    explain_p.set_defaults(func=_cmd_explain)

    budget_p = inner.add_parser("budget", help="what a run cost, per task")
    budget_p.add_argument("run_id")
    budget_p.add_argument("--state-dir", default=None)
    budget_p.add_argument("--json", action="store_true")
    budget_p.set_defaults(func=_cmd_budget)

    cancel_p = inner.add_parser("cancel", help="ask a running scheduler to stop")
    cancel_p.add_argument("run_id")
    cancel_p.add_argument("--state-dir", default=None)
    cancel_p.set_defaults(func=_cmd_cancel)


# --- run ------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    from vise.runtime.adapters.claude_code import ClaudeCodeWorker
    from vise.runtime.artifacts import ArtifactStore
    from vise.runtime.context import ContextResolver
    from vise.runtime.contracts import RunBudget, RunSpec
    from vise.runtime.planner import plan
    from vise.runtime.scheduler import Scheduler, SchedulerConfig, new_run_id
    from vise.runtime.state import runtime_root

    graph_path = Path(args.graph)
    node_id, tasks = _load_node_tasks(graph_path, args.node)
    budget = RunBudget(
        max_cost_usd=args.max_cost or 0.0,
        max_parallel=args.max_parallel,
        max_workers=args.max_workers or 0,
        max_wall_time_s=args.max_wall_time or 0.0,
    )

    preview = plan(
        tasks, budget=budget,
        project_dir=str(Path(args.project_dir).resolve()),
        change=args.change or "",
    )
    print(f"node: {node_id}  ({graph_path.name})\n")
    print(preview.render())
    if preview.problems:
        print("\nrefusing to run a plan with problems.", file=sys.stderr)
        return 1
    if not args.yes:
        print(
            f"\nnothing dispatched. Re-run with --yes to spend roughly "
            f"${preview.estimated_cost_usd:.2f}."
        )
        return 0

    root = Path(args.state_dir) if args.state_dir else runtime_root()
    run_id = args.run_id or new_run_id()
    spec = RunSpec(
        run_id=run_id,
        goal=args.goal or f"{graph_path.stem}:{node_id}",
        project_dir=str(Path(args.project_dir).resolve()),
        graph_name=graph_path.stem,
        node_id=node_id,
        budget=budget,
    )
    scheduler = Scheduler(
        worker=ClaudeCodeWorker(
            project_dir=spec.project_dir,
            permission_mode=args.permission_mode,
        ),
        artifacts=ArtifactStore(root, run_id),
        context=ContextResolver(project_dir=spec.project_dir),
        state_root=root,
        config=SchedulerConfig(
            verify=not args.no_verify,
            isolate=args.isolate,
            spec_change=args.change or "",
        ),
    )
    print(f"\nrun {run_id} — state in {root / 'runs' / run_id}\n")
    state = scheduler.run(spec, tasks)
    print(_render_state(state))

    # What the run learned goes where the next plan will look. After the run,
    # by the process that owns it, and never able to change its exit code.
    from vise.runtime.lessons import record_run_lessons

    recorded = record_run_lessons(state, spec.project_dir)
    if recorded:
        print(f"\n{recorded} lesson(s) recorded in this project's experience memory")
    return 0 if state.succeeded() else 1


def _resolve_graph(state, override: str | None) -> Path:
    """Find the graph a recorded run was planned from.

    ``RunSpec`` stores the file stem and the node id, not the path — so the
    same scope search ``graph_activate`` uses answers it, and a graph that has
    since moved is named with ``--graph`` rather than guessed at.
    """
    if override:
        return Path(override)
    from vise.engines.workflow_scope import resolve_workflow_dirs

    stem = state.spec.graph_name or ""
    if not stem:
        print("vise runtime: this run did not record a graph name; pass --graph",
              file=sys.stderr)
        raise SystemExit(2)
    for _scope, directory in reversed(resolve_workflow_dirs(state.spec.project_dir)):
        for name in (f"{stem}.yaml", f"{stem}-graph.yaml"):
            candidate = directory / name
            if candidate.is_file():
                return candidate
    print(f"vise runtime: cannot find the graph {stem!r} this run used — pass --graph",
          file=sys.stderr)
    raise SystemExit(2)


def _cmd_resume(args: argparse.Namespace) -> int:
    from vise.runtime.adapters.claude_code import ClaudeCodeWorker
    from vise.runtime.artifacts import ArtifactStore
    from vise.runtime.context import ContextResolver
    from vise.runtime.contracts import TaskState
    from vise.runtime.scheduler import Scheduler, SchedulerConfig
    from vise.runtime.state import runtime_root

    from vise.runtime.compose import classification_of
    from vise.runtime.replan import REPLAN_KINDS

    state = _load_state(args, args.run_id)
    remaining = [
        r.task_id for r in state.tasks.values() if r.state is not TaskState.SUCCEEDED
    ]
    print(_render_state(state))
    if not remaining:
        # Exit 3, the same code `compose` uses for the same condition, so a
        # script can tell "this run is finished" from "the resume worked".
        print("\nevery task succeeded — nothing to resume.")
        return 3

    print(f"\nwould retry {len(remaining)}: {', '.join(sorted(remaining))}")

    # A task the runtime itself classified as the plan's fault is not made
    # right by running it again — `compose` says so in as many words, and
    # resume used to re-queue it without a word. Naming it is enough: the
    # caller may have changed the graph, and refusing would make resume
    # useless in exactly the case it is most needed.
    replan_kinds = {k.value for k in REPLAN_KINDS}
    mis_planned = sorted(
        r.task_id for r in state.tasks.values()
        if r.task_id in remaining and classification_of(r) in replan_kinds
    )
    if mis_planned:
        print(
            f"  {', '.join(mis_planned)} failed because the plan was wrong, not the "
            f"work — retrying against the same graph will fail the same way.\n"
            f"  `vise runtime compose {state.spec.run_id}` says what the next plan needs."
        )
    if state.human_gate:
        print(f"clearing the human gate: {state.human_gate}")
    print(f"already spent ${state.ledger.spent.cost_usd:.2f}, which still counts "
          f"against this run's ceiling")
    if not args.yes:
        print("\nnothing dispatched. Re-run with --yes.")
        return 0

    graph_path = _resolve_graph(state, args.graph)
    _node_id, tasks = _load_node_tasks(graph_path, state.spec.node_id)
    project_dir = args.project_dir or state.spec.project_dir
    root = Path(args.state_dir) if args.state_dir else runtime_root()

    scheduler = Scheduler(
        worker=ClaudeCodeWorker(
            project_dir=project_dir, permission_mode=args.permission_mode,
        ),
        artifacts=ArtifactStore(root, state.spec.run_id),
        context=ContextResolver(project_dir=project_dir),
        state_root=root,
        config=SchedulerConfig(
            verify=not args.no_verify,
            isolate=args.isolate,
            spec_change=args.change or "",
        ),
    )
    print(f"\nresuming {state.spec.run_id} — state in {root / 'runs' / state.spec.run_id}\n")
    state = scheduler.resume(state, tasks)
    print(_render_state(state))

    from vise.runtime.lessons import record_run_lessons

    recorded = record_run_lessons(state, project_dir)
    if recorded:
        print(f"\n{recorded} lesson(s) recorded in this project's experience memory")
    return 0 if state.succeeded() else 1


def _cmd_continue(args: argparse.Namespace) -> int:
    """Run a composed plan as the continuation of a recorded one.

    The bookkeeping the compose brief states but cannot act on: what the prior
    run paid for, and how much it spent. Deciding what the next plan should be
    is judgement and stays with whoever composed the graph.

    Dispatches nothing without ``--yes``, exactly like ``run``. Closing the
    loop's bookkeeping is not a reason to reopen its authority.
    """
    from vise.runtime.adapters.claude_code import ClaudeCodeWorker
    from vise.runtime.artifacts import ArtifactStore
    from vise.runtime.context import ContextResolver
    from vise.runtime.contracts import RunBudget, RunSpec
    from vise.runtime.planner import plan
    from vise.runtime.scheduler import Scheduler, SchedulerConfig, new_run_id
    from vise.runtime.state import runtime_root

    prior = _load_state(args, args.run_id)
    already_done = sorted(prior.completed_ids())
    project_dir = str(Path(args.project_dir or prior.spec.project_dir).resolve())

    graph_path = Path(args.graph)
    node_id, tasks = _load_node_tasks(graph_path, args.node)
    budget = RunBudget(max_cost_usd=args.max_cost or 0.0, max_parallel=args.max_parallel)

    # A composed graph is self-contained. It cannot depend on a task the prior
    # run ran — `Graph.validate` refuses a dependency on an id the node does
    # not declare, and that check is right: in a workflow file an unknown
    # dependency is a typo. What the prior run left behind is on disk, not in
    # the schedule.
    #
    # So by default the plan runs what it declares, including a task whose id
    # succeeded before: declaring it is what asking for it looks like, and
    # nothing here can tell whether two tasks sharing an id are the same work.
    # `--skip-done` is for the caller who knows they are.
    preview = plan(
        tasks, budget=budget,
        completed=already_done if args.skip_done else (),
        project_dir=project_dir, change=args.change or "",
        spent_usd=prior.ledger.spent.cost_usd,
    )
    print(f"continuing {prior.spec.run_id} — node {node_id} of {graph_path.name}\n")
    if already_done:
        verb = "skipping" if args.skip_done else "paid for"
        print(f"{verb} from {prior.spec.run_id}: {', '.join(already_done)}")
        if not args.skip_done:
            redeclared = sorted({t.id for t in tasks} & set(already_done))
            if redeclared:
                verb = "appears" if len(redeclared) == 1 else "appear"
                print(
                    f"  {', '.join(redeclared)} {verb} in this plan too and will "
                    f"run again — pass --skip-done if that is not what you meant"
                )
    spent = prior.ledger.spent.cost_usd
    print(f"inherited spend: ${spent:.2f} — a --max-cost bounds the chain, not this link\n")
    print(preview.render())

    if preview.problems:
        print("\nrefusing to continue into a plan with problems.", file=sys.stderr)
        return 1
    if not preview.task_count:
        print("\nthe composed plan has nothing left to do.")
        return 3
    if not args.yes:
        print(
            f"\nnothing dispatched. Re-run with --yes to spend roughly "
            f"${preview.estimated_cost_usd:.2f} on top of ${spent:.2f}."
        )
        return 0

    root = Path(args.state_dir) if args.state_dir else runtime_root()
    run_id = args.new_run_id or new_run_id()
    spec = RunSpec(
        run_id=run_id,
        goal=args.goal or prior.spec.goal,
        project_dir=project_dir,
        graph_name=graph_path.stem,
        node_id=node_id,
        budget=budget,
        parent_run_id=prior.spec.run_id,
    )
    scheduler = Scheduler(
        worker=ClaudeCodeWorker(
            project_dir=project_dir, permission_mode=args.permission_mode,
        ),
        artifacts=ArtifactStore(root, run_id),
        context=ContextResolver(project_dir=project_dir),
        state_root=root,
        config=SchedulerConfig(
            verify=not args.no_verify,
            isolate=args.isolate,
            spec_change=args.change or "",
        ),
    )
    print(f"\nrun {run_id} — state in {root / 'runs' / run_id}\n")
    state = scheduler.continue_from(prior, spec, tasks)
    print(_render_state(state))

    from vise.runtime.lessons import record_run_lessons

    recorded = record_run_lessons(state, project_dir)
    if recorded:
        print(f"\n{recorded} lesson(s) recorded in this project's experience memory")
    return 0 if state.succeeded() else 1


def _cmd_compose(args: argparse.Namespace) -> int:
    """Read a run into a brief for composing what follows it.

    Dispatches nothing. There is no ``run_start`` tool by a documented
    decision, and this does not reopen it: the composed graph is something a
    person reads and runs.
    """
    from vise.runtime.compose import brief_from

    brief = brief_from(_load_state(args, args.run_id))
    if args.json:
        print(json.dumps(brief.to_dict(), indent=2))
    else:
        print(brief.render())
    # Zero means "there is a plan to write". A finished run has nothing to
    # compose, which is not an error but is worth a distinct code so a script
    # can tell the two apart.
    return 0 if brief.needs_a_new_plan else 3


# --- reading a run back ---------------------------------------------------


def _load_state(args: argparse.Namespace, run_id: str):
    from vise.runtime.state import RunState, runtime_root

    root = Path(args.state_dir) if args.state_dir else runtime_root()
    state = RunState.load(root, run_id)
    if state is None:
        print(f"vise runtime: no run {run_id!r} under {root}", file=sys.stderr)
        raise SystemExit(2)
    return state


def _run_ids(args: argparse.Namespace) -> list[str]:
    from vise.runtime.state import runtime_root

    root = Path(args.state_dir) if args.state_dir else runtime_root()
    runs = root / "runs"
    if not runs.is_dir():
        return []
    return sorted(
        (p.name for p in runs.iterdir() if (p / "state.json").is_file()),
        reverse=True,
    )


def _render_state(state) -> str:
    lines = [f"run {state.spec.run_id} — {state.spec.goal}"]
    if state.spec.parent_run_id:
        # Without this a chain reads as unrelated runs, and the money it spent
        # cannot be added up by anyone who was not there when it was started.
        lines.append(f"  continues {state.spec.parent_run_id}")
    if state.cancelled:
        lines.append(f"CANCELLED: {state.cancel_reason}")
    elif state.human_gate:
        lines.append(f"WAITING ON A PERSON: {state.human_gate}")
    elif state.succeeded():
        lines.append("every task succeeded")
    for record in sorted(state.tasks.values(), key=lambda r: r.task_id):
        agent = record.agent_id or "—"
        model = f"{record.model}/{record.effort}" if record.model else "—"
        attempts = f" ({record.attempt_count} attempts)" if record.attempt_count > 1 else ""
        lines.append(f"  {record.task_id:<24} {record.state.value:<14} {agent:<18} {model}{attempts}")
        if record.note and record.state.value not in ("succeeded",):
            lines.append(f"      {record.note}")
    spent = state.ledger.spent
    lines.append(f"cost: ${spent.cost_usd:.2f}  tokens: {spent.tokens_in}in/{spent.tokens_out}out")
    return "\n".join(lines)


def _cmd_status(args: argparse.Namespace) -> int:
    if args.run_id:
        print(_render_state(_load_state(args, args.run_id)))
        return 0
    ids = _run_ids(args)
    if not ids:
        print("no runs recorded yet")
        return 0
    for run_id in ids[: args.limit]:
        print(_render_state(_load_state(args, run_id)))
        print()
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    """Every scheduler decision, in order.

    This is the command that answers "why did this cost $1.83" and "why did task
    17 end up on opus". A router whose choices cannot be read back is a router
    nobody can correct.
    """
    state = _load_state(args, args.run_id)
    if args.json:
        print(json.dumps({"run_id": state.spec.run_id, "events": state.events}, indent=2))
        return 0
    print(f"run {state.spec.run_id} — {state.spec.goal}\n")
    for event in state.events:
        kind = event.get("kind", "?")
        task = event.get("task")
        head = f"  {kind:<20} {task or ''}"
        detail = {
            k: v for k, v in event.items()
            if k not in ("ts", "kind", "task") and v not in (None, "", [], {})
        }
        print(head + ("  " + json.dumps(detail, default=str) if detail else ""))
    return 0


def _cmd_budget(args: argparse.Namespace) -> int:
    state = _load_state(args, args.run_id)
    report = state.ledger.report()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"run {state.spec.run_id}")
    print(f"  spent:     ${report['spent']['cost_usd']:.4f}")
    remaining = report["remaining_usd"]
    print(f"  remaining: {'unset' if remaining is None else f'${remaining:.4f}'}")
    print(f"  workers:   {report['workers_started']}")
    if report["unset_ceilings"]:
        print(f"  unset ceilings: {', '.join(report['unset_ceilings'])}")
    print("  per task:")
    for task_id, usage in report["by_task"].items():
        print(f"    {task_id:<28} ${usage['cost_usd']:.4f}")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    from vise.runtime.state import request_cancel, runtime_root

    root = Path(args.state_dir) if args.state_dir else runtime_root()
    path = request_cancel(root, args.run_id)
    print(f"cancel requested for {args.run_id} ({path})")
    print("a running scheduler stops before its next dispatch; one already "
          "finished will not notice.")
    return 0
