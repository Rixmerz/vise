"""May this run dispatch work that writes? — see docs/scheduler.md § The spec gate.

`mandatory-openspec-gate` made vise's spec phase impossible to talk past, and
`skills/orchestration/SKILL.md` says why delegation is not an escape hatch: a
subagent hits the same gate, because it is a node gate rather than a prompt.

The execution plane broke that. A `dag` node's tasks never traverse the graph —
the scheduler turns them straight into subprocesses — so they reach no node gate
at all. This module is the gate on that path, and it deliberately asks a
narrower question than the node gate does: not "may the workflow advance" but
"may N briefs become N processes that can write to this tree".

Two properties it inherits from `openspec_profile`, both load-bearing:

**It never shells out.** The `openspec` CLI is a Node package. A gate that goes
red because a teammate has not run `npm i -g` teaches people to set
`VISE_NODE_GATE_OVERRIDE=1`, and an override habit is worse than no gate. Every
answer here is stdlib string work over files the repo already owns.

**It never raises.** An unreadable planning tree is "no well-formed change" —
a red gate with an accurate reason, not a crash that takes the run down.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from vise.engines.openspec_profile import ChangeInfo, active_changes, openspec_root

#: The one bypass in the product. Deliberately the same variable the node gate
#: honours, and deliberately not a CLI flag: a bypass that costs one keystroke
#: and leaves no trace is not a gate, it is a default.
OVERRIDE_ENV = "VISE_NODE_GATE_OVERRIDE"


@dataclass(frozen=True)
class SpecGateVerdict:
    """Whether a run may dispatch, and the whole argument for the answer."""

    ok: bool
    reason: str = ""
    change: str | None = None
    overridden: bool = False

    def render(self) -> str:
        if self.overridden:
            return f"spec gate overridden — {self.reason}"
        if self.ok:
            return f"spec gate: {self.reason}"
        return f"spec gate blocked: {self.reason}"


def _well_formed(change: ChangeInfo) -> bool:
    """A change a run may implement: a stated intent and a readable delta.

    Not ``tasks_complete``. That is the bar the node gate uses on the edge into
    the irreversible phase, where the work is already done — here the run is
    what ticks those boxes, and requiring them first would gate the work on its
    own output.
    """
    return change.has_proposal and change.deltas.well_formed


def _why_not(change: ChangeInfo) -> str:
    """The precise gap in one change, so the fix does not need a source read."""
    if not change.has_proposal:
        return f"change {change.name!r} has no proposal.md"
    d = change.deltas
    if not d.files:
        return f"change {change.name!r} has no specs/**/*.md delta"
    if d.headers == 0:
        return (
            f"change {change.name!r} has delta files but no delta header "
            f"(## ADDED|MODIFIED|REMOVED|RENAMED Requirements)"
        )
    if d.orphan_requirements:
        names = ", ".join(d.orphan_requirements[:3])
        return (
            f"change {change.name!r} has requirement(s) with no "
            f"#### Scenario: — {names}"
        )
    if d.requirements == 0:
        return f"change {change.name!r} declares no ### Requirement:"
    return f"change {change.name!r} is not well-formed"


def check(
    project_dir: str | Path,
    *,
    change: str = "",
    writes: bool = True,
) -> SpecGateVerdict:
    """Decide whether a run may start.

    ``writes`` is the run's own answer to "does anything here change the tree".
    A run where nothing does cannot change the system's contract, so there is
    nothing for a specification to describe and the gate stands aside — its red
    should always mean "you are about to build something nobody wrote down".
    """
    if not writes:
        return SpecGateVerdict(True, "no task in this run writes — nothing to specify")

    overridden = os.environ.get(OVERRIDE_ENV) == "1"

    root = openspec_root(project_dir)
    if root is None:
        return _blocked(
            "this project has no openspec/ root — run `openspec init`, then "
            "write the change this run implements",
            overridden,
        )

    changes = active_changes(project_dir)
    if not changes:
        return _blocked(
            f"{root} has no active change — run `openspec new change <name>` "
            f"and write its proposal and spec deltas",
            overridden,
        )

    if change:
        pinned = next((c for c in changes if c.name == change), None)
        if pinned is None:
            found = ", ".join(sorted(c.name for c in changes)) or "none"
            return _blocked(
                f"change {change!r} not found under {root / 'changes'} "
                f"(active: {found})",
                overridden,
            )
        if not _well_formed(pinned):
            return _blocked(_why_not(pinned), overridden)
        return SpecGateVerdict(True, f"change {pinned.name!r} is well-formed",
                               change=pinned.name)

    for candidate in changes:
        if _well_formed(candidate):
            return SpecGateVerdict(
                True, f"change {candidate.name!r} is well-formed",
                change=candidate.name,
            )

    # Several changes, none usable. Report the closest one rather than a count:
    # "3 changes, none well-formed" is true and unactionable.
    return _blocked(_why_not(changes[0]), overridden)


def _blocked(reason: str, overridden: bool) -> SpecGateVerdict:
    """A refusal, or the same refusal walked through and recorded as such.

    An overridden gate reports ``ok`` — the run proceeds — but never reports
    itself as having passed. The distinction is the whole value of the
    override: an audit that cannot tell a met gate from a bypassed one is not
    an audit.
    """
    if overridden:
        return SpecGateVerdict(True, reason, overridden=True)
    return SpecGateVerdict(False, reason)
