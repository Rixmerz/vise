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
import os
import shutil
import subprocess
import tempfile
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
    """Content hash of the working tree in ``directory``, plus ``HEAD``.

    Built by writing a real git tree object from a *scratch* index: copy the
    repo's index to a temp file (which keeps git's stat cache, so this stays
    cheap), ``git add -A`` into that copy, and ``git write-tree``. The result is
    a SHA over the actual bytes of every tracked and untracked-but-not-ignored
    file. Neither the working tree nor the repo's real index is touched.

    Hashing ``git status --porcelain`` instead — which is what this did — hashes
    git's *summary* of the tree. Status prints ``  M app.py`` no matter how much
    of app.py changed, so a task editing a file another task had already dirtied
    produced a hash identical to its own baseline, and rule 3 read real work as
    "nothing was written". Content is the only thing that answers the question
    rule 3 asks.

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
        with tempfile.TemporaryDirectory() as scratch:
            index = Path(scratch) / "index"
            real = _git(directory, "rev-parse", "--git-path", "index")
            if real is None:
                return None
            source = Path(directory) / real.strip() if real.strip() else None
            if source is not None and source.is_file():
                # Carry the stat cache over; without it git re-hashes every file
                # in the repo on every call.
                shutil.copyfile(source, index)
            env = {**os.environ, "GIT_INDEX_FILE": str(index)}
            if _git(directory, "add", "-A", env=env) is None:
                return None
            tree = _git(directory, "write-tree", env=env)
            if tree is None:
                return None
        head = _git(directory, "rev-parse", "HEAD")
    except OSError:
        return None
    return hashlib.sha256((tree + (head or "")).encode()).hexdigest()


def _git(
    directory: str | Path, *args: str, env: dict[str, str] | None = None
) -> str | None:
    """Run one git command for the hash. None on any failure — never raises.

    Output is read as bytes and decoded with ``surrogateescape``: a repository
    holding a latin-1 source file is not a reason for the honesty gate to blow
    up the run.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(directory), capture_output=True, timeout=_GIT_TIMEOUT_S, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "surrogateescape")


def check_result(
    brief: TaskBrief,
    result: TaskResult,
    *,
    baseline_tree: str | None = None,
    current_tree: str | None = None,
    foreign_ownership: tuple[str, ...] = (),
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

    # Paths another task was concurrently entitled to write are not this task's
    # to answer for. Changed paths come from a git diff of one shared working
    # tree, and a diff cannot tell whose file is whose — so without this, every
    # parallel run refuses both writers for each other's work.
    #
    # The cost is real and bounded: a task that writes into a concurrent peer's
    # territory is excused for exactly as long as that peer is in flight. The
    # fix that removes the cost rather than bounding it is per-task worktree
    # isolation, where the diffs never share a tree.
    escaped = _own.escaped(result.changed_paths, brief.ownership)
    if escaped and foreign_ownership:
        escaped = tuple(p for p in escaped if not _own.matches(p, foreign_ownership))
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
