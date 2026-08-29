"""The gates a worker's own claim has to get past — docs/worker-contract.md.

A worker reports ``pass``. That is a claim, and these four rules are what a claim
must survive before the runtime treats it as a result. None of them judges
content: each refuses a *shape*. A runtime that grades prose is a runtime that
can be argued with, and the thing being checked here is exactly the party doing
the arguing.

**These gates fail closed** — the inverse of vise's hook contract, and
deliberately so. A hook that cannot run must never take the session down; a gate
that cannot evaluate must never report success. Rule 3 is the one exception, and
its open direction is precise rather than convenient: when the tree hash is
*unknowable* it has no finding, because a gate that cannot compute its input and
reports a violation anyway is committing the error it exists to catch.

Three of the four are ported from mini-vise, where they were measured against
real pipeline runs. Rule 3 in particular is the only mechanical honesty check in
either codebase — every other one asks another model, which means every other one
can be talked out of its finding.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from vise.runtime import ownership as _own
from vise.runtime.contracts import TaskBrief, TaskResult, Verdict

#: Roles whose pass must quote a command and its real output. "Tests pass" is
#: not evidence; the text the terminal produced is.
EVIDENCE_ROLES = frozenset({"test", "verify", "qa"})

#: Roles whose pass must quote the repo's *existing* checks. The claim being
#: made is "I did not break what was there" — authoring new tests is a different
#: role's job, and a check the worker wrote itself proves nothing about that.
CHECKS_ROLES = frozenset({"backend", "frontend", "migration", "integration"})

_GIT_TIMEOUT_S = 10


@dataclass(frozen=True)
class GateOutcome:
    """What the gates concluded about one result."""

    accepted: bool
    refusals: tuple[str, ...] = ()
    result: TaskResult | None = None

    def __bool__(self) -> bool:
        return self.accepted


def tree_hash(directory: str | Path | None) -> str | None:
    """Hash of ``git status --porcelain`` plus ``HEAD`` in ``directory``.

    HEAD is in the hash so that a worker which commits its work still moves it.
    Without HEAD the tree goes clean on commit, a commit becomes
    indistinguishable from doing nothing, and rule 3 wedges on exactly the
    workers doing the right thing.

    Returns None for every degraded case — no directory, git not on PATH, not a
    repository, subprocess error or timeout. The caller reads None as "nothing to
    compare", never as "unchanged".
    """
    if not directory:
        return None
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(directory), capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(directory), capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None
    head_out = head.stdout if head.returncode == 0 else ""
    return hashlib.sha256((status.stdout + head_out).encode()).hexdigest()


def check_result(
    brief: TaskBrief,
    result: TaskResult,
    *,
    baseline_tree: str | None = None,
    current_tree: str | None = None,
) -> GateOutcome:
    """Run the four gates against one claimed result.

    A refused pass comes back as a ``FAIL`` carrying every refusal in its
    summary — not raised. The run continues; what changes is that the task did
    not succeed, its failure is on the record with a reason, and the next
    attempt's brief carries it.
    """
    if result.verdict is not Verdict.PASS:
        return GateOutcome(True, (), result)

    refusals: list[str] = []

    if brief.role in EVIDENCE_ROLES and not result.evidence.strip():
        refusals.append(
            f"role '{brief.role}' passed without evidence — the command run and its "
            f"real output, verbatim, not a claim that it passed"
        )

    if brief.role in CHECKS_ROLES and not result.checks.strip():
        refusals.append(
            f"role '{brief.role}' passed without checks — the repo's existing checks, "
            f"run and quoted. The claim is 'I did not break what was there'"
        )

    if brief.writes and baseline_tree is not None and current_tree is not None:
        if baseline_tree == current_tree:
            refusals.append(
                "passed with an unchanged tree — neither the working tree nor HEAD "
                "moved since the task started, so nothing was written or committed"
            )

    escaped = _own.escaped(result.changed_paths, brief.ownership)
    if escaped:
        refusals.append(
            "wrote outside its declared ownership: "
            + ", ".join(sorted(escaped))
            + f" (claimed: {', '.join(brief.ownership) or '(none declared)'})"
        )

    if not refusals:
        return GateOutcome(True, (), result)

    summary = "; ".join(refusals)
    downgraded = replace(
        result,
        verdict=Verdict.FAIL,
        summary=f"refused by the honesty gates: {summary}"
        + (f" | worker said: {result.summary}" if result.summary else ""),
    )
    return GateOutcome(False, tuple(refusals), downgraded)
