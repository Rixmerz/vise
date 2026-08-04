"""Read a project's OpenSpec planning state straight off disk — no CLI needed.

OpenSpec (github.com/Fission-AI/OpenSpec) keeps spec-driven work under an
``openspec/`` root::

    openspec/
      config.yaml
      specs/<capability>/spec.md          # what IS built
      changes/<change-id>/
        .openspec.yaml                    # marks the dir as a change
        proposal.md                       # why & what
        design.md                         # how (optional)
        tasks.md                          # implementation checklist
        specs/<capability>/spec.md        # the DELTA for this change
      changes/archive/<change-id>/        # landed, folded into specs/

This module is the reason the OpenSpec gate can be **mandatory** rather than
advisory. The ``openspec`` CLI is a Node package; making a phase gate depend on
it would hard-block every repo that has not run ``npm i -g``, which is exactly
the failure mode ``.vise/quality.yaml`` warns about ("a gate that goes red for
environment reasons is worse than no gate: it teaches you to pass
VISE_NODE_GATE_OVERRIDE=1"). Everything here is stdlib string work against
files the repo already owns, so it runs everywhere, always.

It is deliberately a STRUCTURAL reader, not a validator: presence, shape, and
counts. Semantic validation stays with ``openspec validate --strict``, which
the ``openspec`` validator runs as a separate, degradable tier.

Read on every traverse — must never raise. Every malformed/missing input
degrades to an empty result instead of an exception.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

OPENSPEC_ROOT_ENV = "VISE_OPENSPEC_ROOT"

# Delta headers are the load-bearing bit of an OpenSpec change: a change with a
# specs/ tree but no `## ADDED Requirements` header parses to zero deltas and
# `openspec validate` rejects it. Matching the same four verbs the CLI does.
_DELTA_HEADER_RE = re.compile(r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\s*$")
_REQUIREMENT_RE = re.compile(r"^###\s+Requirement:\s*(\S.*?)\s*$")
_SCENARIO_RE = re.compile(r"^####\s+Scenario:\s*(\S.*?)\s*$")
_TASK_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

#: Directory names under ``changes/`` that are never an active change.
_NOT_A_CHANGE = frozenset({"archive"})


@dataclass(frozen=True, slots=True)
class DeltaStats:
    """What the delta specs under one change actually contain."""

    files: tuple[str, ...] = ()
    """Change-relative paths of every ``specs/**/*.md`` found."""

    headers: int = 0
    """Count of ``## ADDED|MODIFIED|REMOVED|RENAMED Requirements`` headers."""

    requirements: int = 0
    """Count of ``### Requirement:`` blocks."""

    orphan_requirements: tuple[str, ...] = ()
    """Requirement titles with zero ``#### Scenario:`` blocks under them.

    The CLI rejects these outright, so they are the single most common reason a
    hand-written change fails validation. Naming them beats a bare "invalid".
    """

    @property
    def well_formed(self) -> bool:
        """A delta set the CLI would accept on shape alone."""
        return bool(self.files) and self.headers > 0 and self.requirements > 0 and not self.orphan_requirements


@dataclass(frozen=True, slots=True)
class ChangeInfo:
    """One active (non-archived) change proposal."""

    name: str
    path: Path
    has_proposal: bool = False
    has_design: bool = False
    has_tasks: bool = False
    tasks_total: int = 0
    tasks_done: int = 0
    deltas: DeltaStats = DeltaStats()

    @property
    def tasks_complete(self) -> bool:
        """Every checklist box ticked. An EMPTY tasks.md is not complete —
        zero-of-zero is the shape a scaffolded-but-unwritten change has, and
        grading that as done is the false green this whole module exists to
        prevent.
        """
        return self.has_tasks and self.tasks_total > 0 and self.tasks_done == self.tasks_total

    @property
    def tasks_summary(self) -> str:
        return f"{self.tasks_done}/{self.tasks_total} tasks"


def _read_lines(path: Path) -> list[str]:
    """File contents as lines, or ``[]`` for anything unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return []


def _strip_fenced(lines: list[str]) -> list[str]:
    """Drop fenced code blocks.

    A template or a proposal that *shows* the delta syntax inside a ``` fence
    would otherwise be counted as declaring real requirements — the reader
    would report a well-formed change that the CLI then rejects.
    """
    out: list[str] = []
    fence: str | None = None
    for line in lines:
        m = _FENCE_RE.match(line)
        if m:
            token = m.group(1)
            if fence is None:
                fence = token
            elif token == fence:
                fence = None
            continue
        if fence is None:
            out.append(line)
    return out


def openspec_root(project_dir: str | Path) -> Path | None:
    """The project's ``openspec/`` directory, or None if it has none.

    ``VISE_OPENSPEC_ROOT`` overrides with an absolute path (used by tests, and
    by repos that keep planning artifacts outside the code tree).
    """
    override = os.environ.get(OPENSPEC_ROOT_ENV, "").strip()
    candidate = Path(override) if override else Path(project_dir) / "openspec"
    try:
        return candidate if candidate.is_dir() else None
    except OSError:
        return None


def _parse_deltas(change_dir: Path) -> DeltaStats:
    specs_dir = change_dir / "specs"
    try:
        if not specs_dir.is_dir():
            return DeltaStats()
        md_files = sorted(p for p in specs_dir.rglob("*.md") if p.is_file())
    except OSError:
        return DeltaStats()

    files: list[str] = []
    headers = 0
    requirements = 0
    orphans: list[str] = []

    for md in md_files:
        files.append(str(md.relative_to(change_dir)))
        current: str | None = None
        saw_scenario = False
        for line in _strip_fenced(_read_lines(md)):
            if _DELTA_HEADER_RE.match(line):
                headers += 1
                continue
            req = _REQUIREMENT_RE.match(line)
            if req:
                if current is not None and not saw_scenario:
                    orphans.append(current)
                current = req.group(1)
                saw_scenario = False
                requirements += 1
                continue
            if _SCENARIO_RE.match(line):
                saw_scenario = True
        if current is not None and not saw_scenario:
            orphans.append(current)

    return DeltaStats(
        files=tuple(files),
        headers=headers,
        requirements=requirements,
        orphan_requirements=tuple(orphans),
    )


def _parse_tasks(tasks_md: Path) -> tuple[int, int]:
    """``(total, done)`` checklist boxes in a tasks.md."""
    total = done = 0
    for line in _strip_fenced(_read_lines(tasks_md)):
        m = _TASK_RE.match(line)
        if m:
            total += 1
            if m.group(1) in ("x", "X"):
                done += 1
    return total, done


def _read_change(change_dir: Path) -> ChangeInfo:
    tasks_md = change_dir / "tasks.md"
    has_tasks = tasks_md.is_file()
    total, done = _parse_tasks(tasks_md) if has_tasks else (0, 0)
    return ChangeInfo(
        name=change_dir.name,
        path=change_dir,
        has_proposal=(change_dir / "proposal.md").is_file(),
        has_design=(change_dir / "design.md").is_file(),
        has_tasks=has_tasks,
        tasks_total=total,
        tasks_done=done,
        deltas=_parse_deltas(change_dir),
    )


def active_changes(project_dir: str | Path) -> list[ChangeInfo]:
    """Every non-archived change under ``openspec/changes/``, sorted by name.

    Never raises: no root, no ``changes/`` dir, or an unreadable tree all give
    ``[]``. Callers distinguish "no openspec at all" from "openspec but no
    change in flight" via :func:`openspec_root`.
    """
    root = openspec_root(project_dir)
    if root is None:
        return []
    changes_dir = root / "changes"
    try:
        if not changes_dir.is_dir():
            return []
        entries = sorted(p for p in changes_dir.iterdir() if p.is_dir())
    except OSError:
        return []

    return [
        _read_change(p)
        for p in entries
        if p.name not in _NOT_A_CHANGE and not p.name.startswith(".")
    ]
