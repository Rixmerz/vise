"""Which candidate moves the decouple phase is allowed to propose.

The phase runs after ``tests_pass`` is green, because that is the first moment
a boundary decision has an oracle. Its inputs come from livespec —
``search_similar`` for the duplicate a diff just created under a different
name, ``analyze_impact`` for what a changed signature reaches. Those tools are
not vise's and vise cannot call them: they live in the session, alongside the
agent running the phase. See ``vise.core.livespec`` for the same boundary
written down for names.

So the seam is here. The agent looks; this module *judges*, and the judgment is
code somebody reviewed rather than a paragraph in a prompt the model may weigh
however it likes. It takes candidates in the shape vise asks for and returns
what may move, what may not, and under which rule — the report a person reads
to decide whether the phase earned its place.

The rules are ``skills/codelayer/SKILL.md`` "When NOT to decouple", and
``test_decouple.py`` holds this module to that section: a rule that drifts from
the skill is a rule an agent was told twice, differently.

Refusals are ordered and only the first is recorded, because a finding that
names three reasons names none. ``out_of_scope`` is tried first — the skill
puts it third but calls it "out of scope *entirely*", which outranks a
measurement — then the skill's own order. A candidate this module cannot
place is refused, not passed: the phase's whole justification is that it
decides late, with evidence, and a guess is not evidence.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: "A module under roughly 100 lines stays one file." — codelayer, Size floor.
SIZE_FLOOR_LINES = 100

#: "Do not extract ... until the third consumer exists." — codelayer, rule of three.
MIN_CONSUMERS = 3

#: Path segments that put a file out of scope entirely. Migrations are
#: append-only history; tests duplicate on purpose; scripts and generated code
#: are not read the way library code is read.
_OUT_OF_SCOPE_SEGMENTS = frozenset({
    "bin",
    "generated",
    "migrate",
    "migration",
    "migrations",
    "node_modules",
    "script",
    "scripts",
    "test",
    "tests",
    "vendor",
})

#: Basename markers for the same, where the directory does not say it.
_OUT_OF_SCOPE_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_pb2", ".g.", ".generated.")


@dataclass(frozen=True)
class Candidate:
    """A unit livespec surfaced, in the shape the refusal rules need.

    ``module_lines`` is the size of the file the unit lives in, not the unit —
    the size floor is about whether a *module* should be split. ``consumers``
    is the count of call sites livespec found, which is what the rule of three
    counts. A caller that cannot fill one of these truthfully should leave it
    at its default and let the candidate be refused.
    """

    unit: str
    path: str
    module_lines: int = 0
    consumers: int = 0


@dataclass(frozen=True)
class Refusal:
    """A candidate that will not be proposed, and the rule that stopped it."""

    candidate: Candidate
    rule: str

    def to_dict(self) -> dict[str, Any]:
        return {"unit": self.candidate.unit, "path": self.candidate.path, "rule": self.rule}


@dataclass
class DecoupleReport:
    """What the phase found, refused, moved and spent.

    ``moved`` and ``reverted`` are filled in by whoever dispatched the builder
    tasks, not by this module — triage happens before anything is touched.
    """

    accepted: list[Candidate] = field(default_factory=list)
    refused: list[Refusal] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    skipped: str = ""

    @property
    def found(self) -> int:
        """Candidates triaged — accepted plus refused."""
        return len(self.accepted) + len(self.refused)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "accepted": [c.unit for c in self.accepted],
            "refused": [r.to_dict() for r in self.refused],
            "moved": list(self.moved),
            "reverted": list(self.reverted),
            "cost_usd": round(self.cost_usd, 4),
            "skipped": self.skipped,
        }


def _out_of_scope(candidate: Candidate) -> bool:
    """Scripts, migrations, generated code and tests — refused categorically."""
    parts = [
        part.lower()
        for part in candidate.path.replace("\\", "/").split("/")
        if part and part not in {".", ".."}
    ]
    if not parts:
        return True
    if _OUT_OF_SCOPE_SEGMENTS.intersection(parts):
        return True
    return any(marker in parts[-1] for marker in _OUT_OF_SCOPE_MARKERS)


def refuse(candidate: Candidate) -> str | None:
    """The rule that refuses this candidate, or ``None`` if none does.

    First match wins, in the order documented at the top of this module.
    """
    if not candidate.unit.strip() or not candidate.path.strip():
        return "out_of_scope"
    if _out_of_scope(candidate):
        return "out_of_scope"
    if candidate.consumers < MIN_CONSUMERS:
        return "rule_of_three"
    if candidate.module_lines < SIZE_FLOOR_LINES:
        return "size_floor"
    return None


def triage(candidates: Iterable[Candidate]) -> DecoupleReport:
    """Split what livespec surfaced into what may move and what may not."""
    report = DecoupleReport()
    for candidate in candidates:
        rule = refuse(candidate)
        if rule is None:
            report.accepted.append(candidate)
        else:
            report.refused.append(Refusal(candidate, rule))
    return report


def skipped(reason: str) -> DecoupleReport:
    """The report for a phase that could not look — no index, no guessing."""
    return DecoupleReport(skipped=reason or "the symbol index could not be read")
