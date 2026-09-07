"""What a run learned, written where the next run will read it.

The experience memory was fed by one hook: the commit recorder, which turns a
commit subject into an entry per touched glob. After a week of dogfooding the
runtime that meant 41 of 42 entries were commit subjects — and the one entry a
future agent would actually want, *which gate failed on which file and why*,
came from the node gate, not from a commit.

The runtime produces that kind of signal on every run and recorded none of it.
A replan is the strongest lesson a run can leave: the task was asked the wrong
question, and the reason is on the record. A run parked for a person, a task
nobody could route, a result the drain could not read — each names something
about this repository that the next plan should know.

Successes are recorded too, and for a reason that is easy to miss: a store of
nothing but failures answers "what goes wrong here" and cannot answer "what
worked". The reusable half of a success is its *cost shape* rather than the fact
of it — a node whose tasks all pass at their policy rung is one nobody needs to
budget a climb for, and a node where the same task always needs a rung more is
one whose policy is short. Neither shows in a single run.

This module reads the run's own event record and writes those as memory. It is
called once, after the run, by the process that owns the run (``vise runtime
run``); the scheduler stays ignorant of memory, which keeps the seam where the
tests already are.

Scope is decided per kind, not once for the module. ``run:<graph>:<node>`` is a
statement about a *workflow*, and a workflow is the same one in every repository
that installs vise — so what a replan and a success learned about a node is
written globally, where a first run in a new repository can read it. What
blocked a run is not: an unroutable role, an ownership overlap, a drain that
could not parse a result are facts about this checkout, and filing them where
every repository reads them would fill the shared store with answers to
questions nobody else is asking.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from vise.engines.experience_memory import (
    ExperienceEntry,
    get_experience_store,
    get_project_experience_store,
)
from vise.runtime.contracts import REPLAN_KINDS, Attempt, Verdict, tag
from vise.runtime.state import RunState

log = logging.getLogger(__name__)

#: Event kinds that mean "this run was stopped or bent by something about the
#: repository", with the severity each deserves in memory. Project-scoped for
#: exactly that reason: the reason names this checkout.
_BLOCKING: dict[str, str] = {
    "human_gate": "high",
    "replan_declined": "high",
    "replan_unavailable": "high",
    "unroutable": "medium",
    "not_admitted": "medium",
    "agent_refused": "medium",
    "drain_failed": "medium",
}


def lessons_from(state: RunState) -> list[ExperienceEntry]:
    """Entries a run leaves behind. Deduplicated within the run."""
    spec = state.spec
    pattern = f"run:{spec.graph_name or '-'}:{spec.node_id or '-'}"
    # A name, as every other writer records it. `_project_multiplier` in the
    # injector compares this against `Path(project_dir).name`, so a full path
    # never matched and marked every runtime lesson foreign to the project that
    # produced it.
    origin = Path(spec.project_dir).name
    out: list[ExperienceEntry] = []
    seen: set[tuple[str, str]] = set()

    def add(entry: ExperienceEntry) -> None:
        key = (entry.type, entry.description)
        if key in seen:
            return
        seen.add(key)
        out.append(entry)

    for event in state.events:
        kind = str(event.get("kind", ""))
        if kind == "replanned":
            add(ExperienceEntry(
                type="run_replanned",
                file_pattern=pattern,
                keywords=["replan", spec.graph_name or "", spec.node_id or ""],
                domain="runtime",
                description=(
                    f"replan #{event.get('replans', state.replans)} in run "
                    f"{spec.run_id}: {spec.goal}"
                ),
                resolution=_replan_reasons(state),
                severity="high",
                scope="global",
                project_origin=origin,
            ))
        elif kind in _BLOCKING:
            task = event.get("task")
            reason = str(event.get("reason") or event.get("future_kind") or "")
            where = f"task {task}: " if task else ""
            # An entry whose whole content is a dangling em-dash is retrieved
            # and shown to future agents exactly like a useful one. Write the
            # separator only when there is something after it.
            detail = f"{where}{reason}".strip()
            said = f"{kind} in run {spec.run_id}"
            add(ExperienceEntry(
                type="run_blocked",
                file_pattern=pattern,
                keywords=[kind, str(task or ""), spec.graph_name or ""],
                domain="runtime",
                description=(f"{said} — {detail}" if detail else said)[:300],
                resolution="",
                severity=_BLOCKING[kind],
                scope="project",
                project_origin=origin,
            ))

    # Read off the final state rather than an event, because "every task
    # succeeded" is a property of the record and no single event asserts it.
    # A run that replanned and then succeeded leaves both entries, which is the
    # pair worth having: the first shape was wrong for this reason, the second
    # worked and cost this much.
    if state.succeeded():
        add(ExperienceEntry(
            type="run_succeeded",
            file_pattern=pattern,
            keywords=["succeeded", spec.graph_name or "", spec.node_id or ""],
            domain="runtime",
            description=(
                f"{len(state.tasks)} task(s) succeeded in run {spec.run_id}: {spec.goal}"
            ),
            resolution=_success_shape(state),
            severity="low",
            scope="global",
            project_origin=origin,
        ))
    return out


def _success_shape(state: RunState) -> str:
    """What it took to get there, for a run where every task passed.

    Tasks that passed on the first attempt are counted, not listed: naming
    fifteen tasks that did the expected thing buries the one that did not, and
    the one that did not is the whole lesson.

    A run where nothing climbed still says so rather than returning empty. "Every
    task passed on its first attempt" is a finding about the plan's sizing, and
    an empty resolution is the defect this module was just fixed for.
    """
    spent = state.ledger.spent
    climbed = [
        record for _, record in sorted(state.tasks.items())
        if record.attempt_count > 1
    ]
    replans = f"{state.replans} replan(s)" if state.replans else "no replans"
    head = f"{len(state.tasks)} task(s), {replans}, ${spent.cost_usd:.2f}"
    if not climbed:
        return f"{head} — every task passed on its first attempt"
    lines = [head]
    for record in climbed:
        said = (record.result.summary if record.result else "") or record.note or "(no summary)"
        lines.append(
            f"{record.task_id} (landed at {tag(record.model, record.effort)} after "
            f"{record.attempt_count} attempts): {said}"
        )
    return "\n".join(lines)[:1000]


def _rungs_tried(attempts: list[Attempt]) -> str:
    """The distinct rungs a task was attempted at, cheapest first, in order.

    This is the strategy half of the lesson. "It failed" is not reusable; "it
    failed at all four rungs" says the ladder was not the missing piece, and a
    later plan reading that knows not to budget for the climb again.
    """
    seen: list[str] = []
    for attempt in attempts:
        label = tag(attempt.model, attempt.effort)
        if label and label not in seen:
            seen.append(label)
    return " → ".join(seen)


def _replan_reasons(state: RunState) -> str:
    """What was tried and what it said, for every task that failed on the way here.

    Not only the tasks classified as plan-level. A task that burned the whole
    ladder on one wrong answer *is* why the run replanned, and filtering to
    ``SPEC_BUG`` and ``ARCHITECTURE_BUG`` left exactly that case writing an empty
    string: the memory recorded that a replan happened and nothing about what had
    been tried, which is the half a later plan actually needs.

    A task with one failure and no plan-level classification is left out on
    purpose. One failure followed by a pass is the ladder working — that task
    recovered, and filing it here would put a solved problem in front of the next
    plan as though it were an open one.
    """
    kinds = {k.value for k in REPLAN_KINDS}
    lines: list[str] = []
    for task_id, record in sorted(state.tasks.items()):
        # The reason and the classification travel together. After the replan
        # the task usually runs again and succeeds, so `record.result` is the
        # success — reading its summary would file "done" as the lesson. The
        # attempt that carried the classification is the one that said why.
        classified: tuple[str, str] | None = None
        if record.result is not None and record.result.classification is not None:
            classified = (str(record.result.classification), record.result.summary)
        else:
            for attempt in reversed(record.attempts):
                if getattr(attempt, "classification", None):
                    classified = (str(attempt.classification), attempt.summary)
                    break
        if classified is None:
            continue
        failures = [a for a in record.attempts if a.verdict is not Verdict.PASS]
        if classified[0] not in kinds and len(failures) < 2:
            continue
        kind, said = classified
        rungs = _rungs_tried(failures)
        head = f"{task_id} ({kind}, tried {rungs})" if rungs else f"{task_id} ({kind})"
        lines.append(f"{head}: {said or record.note or '(no summary)'}")
    return "\n".join(lines)[:1000]


def record_run_lessons(state: RunState, project_dir: str) -> int:
    """Write the run's lessons where each one's scope says. Never raises.

    Each entry names its own scope and this routes on it. Writing them all to the
    project store regardless — which is what this did — made ``scope`` a field
    that described nothing: a `run_succeeded` entry could say "global" and still
    be readable only from the repository that produced it.

    Returns how many were recorded. A memory that cannot be written is logged and
    otherwise ignored: the run already happened, and its exit code is about the
    run, not about the bookkeeping after it.
    """
    entries = lessons_from(state)
    if not entries:
        return 0
    written = 0
    for scope in ("project", "global"):
        batch = [e for e in entries if e.scope == scope]
        if not batch:
            continue
        try:
            store = (
                get_project_experience_store(project_dir) if scope == "project"
                else get_experience_store()
            )
            for entry in batch:
                store.record(entry)
            store.save()
            written += len(batch)
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail the run
            # Per scope, so an unwritable global store does not also lose the
            # project lessons it had nothing to do with.
            log.warning("could not record %s run lessons: %s", scope, exc)
    return written


def describe(entries: Iterable[ExperienceEntry]) -> str:
    return "\n".join(f"  {e.type:<14} {e.description[:90]}" for e in entries)
