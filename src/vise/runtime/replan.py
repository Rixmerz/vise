"""What to do when the failure says the *plan* was wrong.

`recovery.decide` has always been able to return `REPLAN`, and the scheduler has
always known how to swap a task graph and keep the work that already passed. The
hook that produces the new graph was never wired, so in production
`config.replanner` was `None` and every spec or architecture failure ended at
`stop_for_human`. This is that hook.

**A replanner recomposes; it never authors a gate.** That boundary is the whole
reason vise can claim anything. Its gates are falsifiable because their pass
conditions are code someone reviewed — `source="mechanical"`, not `"asserted"`.
A planner allowed to write the condition it is judged by is a planner grading its
own homework, which is the exact failure mode this codebase exists to prevent. So
this module adds *tasks*, drawn from roles the registry actually staffs, and it
adds nothing else: no validators, no acceptance criteria of its own invention, no
loosened ownership.

The remediation it inserts is deliberately narrow. A `SPEC_BUG` means the task
was asked the wrong question, and an `ARCHITECTURE_BUG` means the shape it was
asked to build into was wrong. Neither is fixed by running the same task again,
and neither is fixed by this module guessing the answer. What it does is put a
`design` task in front of the failed one, so the next attempt starts from an
answer rather than from the same question — and then lets the same ladder,
budget and gates judge the result.

Bounded by construction: the remediation task's id is derived from the task it
serves, so a second replan of the same task finds it already present and declines
rather than growing the graph. `DEFAULT_MAX_REPLANS` bounds the rest.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from vise.engines.graph_engine import Task
from vise.runtime.contracts import REPLAN_KINDS, TaskState
from vise.runtime.routing import POLICY
from vise.runtime.state import RunState

#: Suffix for a synthesized remediation task. Derived from the task it serves so
#: the same failure cannot add a second one.
REMEDIATION_SUFFIX = "::respec"

#: The role that answers "what should this have been". Staffed by the bundled
#: registry; a project that shadows `design` gets its own agent here for free.
REMEDIATION_ROLE = "design"

#: `design`'s agent, at the *replan* tier. POLICY prices "replan" separately for
#: a reason — it is the decision that redirects every later task — but nothing
#: dispatched a task under that role, so the row priced nobody. Pinning the tier
#: here keeps the row load-bearing without inventing an agent for it.
REMEDIATION_MODEL, REMEDIATION_EFFORT = POLICY["replan"]

_BRIEFS = {
    "spec_bug": (
        "The task below failed because it was asked the wrong question, not "
        "because the work was done badly. Do not implement anything.\n\n"
        "Write down what '{name}' should actually produce: the acceptance "
        "criteria it should have carried, in the project's own terms, and what "
        "about the original framing was wrong. The next attempt starts from "
        "what you write.\n\n"
        "What the failed attempt reported:\n{reason}"
    ),
    "architecture_bug": (
        "The task below failed because the structure it was asked to build "
        "into does not support it. Do not implement anything.\n\n"
        "Describe the smallest structural change that would let '{name}' be "
        "done well, and say what it costs. If the honest answer is that the "
        "task should be split or dropped, say that instead — you are not "
        "obliged to find a way to keep it.\n\n"
        "What the failed attempt reported:\n{reason}"
    ),
}


def default_replanner(
    state: RunState, original: Sequence[Any]
) -> Sequence[Any] | None:
    """Insert a design step ahead of every task whose plan was judged wrong.

    Returns the new task list, or None to decline — which the scheduler reports
    as ``replan_declined`` and stops for a person. Declining is the right answer
    more often than it looks: a replan that cannot name what it would change is
    a replan that would burn a rung to re-run the same thing.
    """
    by_id = {t.id: t for t in original}
    targets = _targets(state, by_id)
    if not targets:
        return None

    added: list[Task] = []
    for task_id in targets:
        task = by_id[task_id]
        remediation_id = f"{task_id}{REMEDIATION_SUFFIX}"
        if remediation_id in by_id:
            # Already tried this once for this task. Growing the graph again
            # would spend money to ask the same question a second time.
            continue
        kind = _classification(state, task_id)
        added.append(
            Task(
                id=remediation_id,
                name=f"Re-specify: {getattr(task, 'name', task_id)}",
                role=REMEDIATION_ROLE,
                prompt=_BRIEFS[kind].format(
                    name=getattr(task, "name", task_id),
                    reason=_reason(state, task_id),
                ),
                dependencies=list(getattr(task, "dependencies", ()) or ()),
                # Reads and reasons; it does not touch the tree. That keeps it
                # out of every ownership partition and off the tree-hash gate,
                # which is honest — its output is an answer, not an edit.
                writes=False,
                # Not a cheap task: it is the one deciding what the expensive
                # ones do next.
                criticality="elevated",
                model=REMEDIATION_MODEL,
                effort=REMEDIATION_EFFORT,
            )
        )
        # The failed task now waits on its own answer.
        task.dependencies = [
            *(getattr(task, "dependencies", ()) or ()), remediation_id
        ]

    if not added:
        return None
    return [*original, *added]


def _targets(state: RunState, by_id: dict[str, Any]) -> list[str]:
    """Tasks whose latest attempt says the plan, not the work, was wrong."""
    out = []
    for task_id, record in state.tasks.items():
        if task_id not in by_id or record.state is TaskState.SUCCEEDED:
            continue
        if _classification(state, task_id) in {k.value for k in REPLAN_KINDS}:
            out.append(task_id)
    return out


def _classification(state: RunState, task_id: str) -> str:
    record = state.tasks.get(task_id)
    if record is None:
        return ""
    if record.result is not None and record.result.classification is not None:
        return str(record.result.classification)
    for attempt in reversed(record.attempts):
        found = getattr(attempt, "classification", None)
        if found:
            return str(found)
    return ""


def _reason(state: RunState, task_id: str) -> str:
    record = state.tasks.get(task_id)
    if record is None:
        return "(no attempt on the record)"
    if record.result is not None and record.result.summary:
        return record.result.summary
    return record.note or "(the attempt reported no summary)"
