"""Rounds until nothing new — docs/scheduler.md § Convergence.

A discovery task is the one shape the runtime could not express. "Find the
duplicated helpers", "find the untested branches", "find the case against" are
not one attempt at a known job; they are a sweep whose right number of passes
is a property of the repository, not of the person writing the YAML. Run once
and the tail is missed, because the last round is where the hard one is. Run a
fixed five and four of them are paid for to report nothing.

So a task may declare ``until`` and the runtime keeps dispatching it while it
keeps finding. Two rules carry the whole design:

- **A round is a passing attempt, and an attempt is not a round.** A failed
  round is a failed attempt and takes the escalation ladder, exactly as it
  would without ``until``. Folding "found nothing" into "failed" is the same
  conflation of ``INCONCLUSIVE`` with ``FAIL`` that ``Verdict`` exists to
  prevent.
- **The deduplication is code.** An agent asked "is this one new" says yes —
  it has every incentive to, and no memory of the other rounds. So the runtime
  keys what each round reported and counts the new ones itself.

Everything here is pure: no I/O, no clock, no model call. The scheduler reads
the list off a round's artifacts (through ``expand.listing``, the one place
that knows how a worker reports a list) and asks this module what it means.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from vise.runtime.expand import text_of

#: Quiet rounds required before a sweep is called done, when the task did not
#: say. Two, not one: a single empty round is as likely to be an agent having a
#: bad pass as it is to be the end of the list, and the second one costs less
#: than the finding it protects against missing.
DEFAULT_STABLE_FOR = 2

#: The most rounds one task may run, when the task did not say. Bounds the
#: money a sweep that never converges can spend before a person sees it.
DEFAULT_MAX_ROUNDS = 5


def key_of(item: Any) -> str:
    """How two findings are told apart. The item's text, verbatim.

    Deliberately exact rather than fuzzy: a near-match rule would decide that
    two genuinely different findings are the same one, and the failure would be
    silent — the second is never reported and nobody knows it existed. Exact
    matching errs the other way, toward one more round, which costs money
    instead of a finding.
    """
    return text_of(item).strip()


@dataclass(frozen=True)
class Round:
    """What one passing round changed about the sweep."""

    #: Items this round reported that no earlier round had.
    fresh: tuple[Any, ...]
    #: Every key found so far, this round included.
    seen: tuple[str, ...]
    #: Consecutive rounds, ending with this one, that added nothing.
    stable: int
    #: How many rounds have run, this one included.
    number: int
    #: Whether the round reported a list at all. A round that reported none is
    #: quiet, not broken — it passed its gates and its verifier, which is a
    #: claim to have looked — but the two are different facts and the record
    #: keeps them apart.
    reported: bool = True

    @property
    def found_something(self) -> bool:
        return bool(self.fresh)


def fold(
    items: Sequence[Any] | None,
    seen: Iterable[str],
    *,
    stable: int,
    number: int,
) -> Round:
    """Fold one round's findings into the sweep so far.

    ``items`` is ``None`` when the round reported no list under the declared
    key. That counts as quiet: the round passed, which is a claim to have
    looked, and treating it as a failure would send a task that genuinely found
    nothing to the escalation ladder.
    """
    already = list(dict.fromkeys(seen))
    known = set(already)
    fresh: list[Any] = []
    for item in items or ():
        key = key_of(item)
        if key and key not in known:
            known.add(key)
            already.append(key)
            fresh.append(item)
    return Round(
        fresh=tuple(fresh),
        seen=tuple(already),
        stable=0 if fresh else stable + 1,
        number=number,
        reported=items is not None,
    )


def another_round(task: Any, outcome: Round) -> bool:
    """Whether the sweep goes again after this round."""
    spec = getattr(task, "until", None)
    if spec is None:
        return False
    if outcome.number >= max(1, spec.max_rounds):
        return False
    return outcome.stable < max(1, spec.stable_for)


def reason(task: Any, outcome: Round) -> str:
    """Why the sweep stopped, in the terms the task declared it in."""
    spec = getattr(task, "until", None)
    if spec is None:
        return ""
    if outcome.number >= max(1, spec.max_rounds):
        return (
            f"stopped at the maximum of {spec.max_rounds} round(s) with "
            f"{len(outcome.seen)} finding(s) — the sweep may not be finished"
        )
    return (
        f"converged: {outcome.stable} round(s) in a row added nothing, "
        f"{len(outcome.seen)} finding(s) in total"
    )


def already_found(seen: Sequence[str], limit: int = 60) -> tuple[str, ...]:
    """The lines a later round's brief carries, so it does not re-report.

    Capped, and the cap is stated in the line the worker reads rather than
    applied behind it: a brief that silently dropped half the list would have
    the round report those again and count as finding something new, which is
    the loop this bound exists to prevent.
    """
    if not seen:
        return ()
    shown = list(seen[:limit])
    head = [
        "already found by earlier rounds of this task — report none of these "
        "again, and say so if you believe one is wrong:"
    ]
    if len(seen) > limit:
        head.append(f"  (showing {limit} of {len(seen)})")
    return tuple(head + [f"  - {line}" for line in shown])
