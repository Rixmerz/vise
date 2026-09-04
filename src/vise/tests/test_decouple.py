"""The decouple phase refuses what the codelayer skill says to refuse.

Two things are pinned here. The first is ordinary: every refusal rule has a
case that trips it and a case that does not, so a rule cannot quietly stop
firing. The second matters more — the rules are prose in
``skills/codelayer/SKILL.md`` and numbers in ``vise.runtime.decouple``, read by
the same agent in the same task. When those two drift, the agent has been told
twice, differently, and the phase's refusals stop meaning what the skill says
they mean. So the constants are asserted against the skill's own sentences.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.runtime.decouple import (
    MIN_CONSUMERS,
    SIZE_FLOOR_LINES,
    Candidate,
    refuse,
    skipped,
    triage,
)

REPO = Path(__file__).resolve().parents[3]
SKILL = REPO / "skills" / "codelayer" / "SKILL.md"

#: A candidate that no rule refuses — big enough module, enough consumers,
#: library code. Every test below is this one, bent on a single axis.
GOOD = Candidate(unit="parse_header", path="src/app/headers.py", module_lines=240, consumers=4)


def _refusal_section() -> str:
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("## When NOT to decouple")
    return text[start : text.index("\n## ", start + 1)]


# --- the rules match the skill they were lifted from ---------------------


def test_the_size_floor_is_the_number_the_skill_states() -> None:
    assert f"roughly {SIZE_FLOOR_LINES} lines" in _refusal_section()


def test_the_rule_of_three_counts_to_the_consumer_the_skill_names() -> None:
    ordinal = {1: "first", 2: "second", 3: "third", 4: "fourth"}[MIN_CONSUMERS]
    assert f"{ordinal} consumer" in _refusal_section()


@pytest.mark.parametrize("kind", ["Scripts", "migrations", "generated code", "tests"])
def test_every_out_of_scope_kind_the_skill_lists_is_refused(kind: str) -> None:
    """The skill names four kinds; each must reach a refusal, not just the prose."""
    assert kind in _refusal_section()
    path = {
        "Scripts": "scripts/build.py",
        "migrations": "db/migrations/0004_add_index.py",
        "generated code": "src/app/generated/schema.py",
        "tests": "src/app/tests/test_headers.py",
    }[kind]
    assert refuse(Candidate(unit="u", path=path, module_lines=400, consumers=9)) == "out_of_scope"


# --- each rule trips, and does not trip -----------------------------------


def test_nothing_refuses_a_candidate_every_rule_allows() -> None:
    assert refuse(GOOD) is None


def test_a_module_under_the_floor_is_refused_by_size_floor() -> None:
    assert refuse(Candidate(**{**vars(GOOD), "module_lines": SIZE_FLOOR_LINES - 1})) == "size_floor"


def test_a_module_exactly_at_the_floor_is_not_refused() -> None:
    assert refuse(Candidate(**{**vars(GOOD), "module_lines": SIZE_FLOOR_LINES})) is None


def test_too_few_consumers_is_refused_by_the_rule_of_three() -> None:
    thin = Candidate(**{**vars(GOOD), "consumers": MIN_CONSUMERS - 1})
    assert refuse(thin) == "rule_of_three"


def test_exactly_three_consumers_is_not_refused() -> None:
    assert refuse(Candidate(**{**vars(GOOD), "consumers": MIN_CONSUMERS})) is None


def test_a_library_path_is_not_out_of_scope() -> None:
    assert refuse(Candidate(**{**vars(GOOD), "path": "src/app/headers.py"})) is None


def test_a_filename_marker_is_enough_when_the_directory_is_not() -> None:
    """`src/app/headers_test.py` sits in library code and is still a test."""
    marked = Candidate(**{**vars(GOOD), "path": "src/app/headers_test.py"})
    assert refuse(marked) == "out_of_scope"


def test_a_file_under_a_scope_directory_is_refused() -> None:
    under = Candidate(**{**vars(GOOD), "path": "src/app/migrations/headers.py"})
    assert refuse(under) == "out_of_scope"


def test_the_same_file_outside_that_directory_is_not() -> None:
    """The directory is what refuses it, not the filename it happens to carry."""
    assert refuse(Candidate(**{**vars(GOOD), "path": "src/app/headers.py"})) is None


def test_a_scope_word_inside_a_longer_filename_does_not_refuse_it() -> None:
    """`migrations` refuses; `migrations_readme.py` is a segment nobody named."""
    assert refuse(Candidate(**{**vars(GOOD), "path": "src/app/migrations_readme.py"})) is None


# --- the order of the rules is a decision, not an accident -----------------


def test_out_of_scope_outranks_a_measurement() -> None:
    """A migration is refused as a migration, whatever its size or call count."""
    both = Candidate(unit="u", path="db/migrations/0004.py", module_lines=3, consumers=0)
    assert refuse(both) == "out_of_scope"


def test_the_rule_of_three_outranks_the_size_floor() -> None:
    """Both apply; the skill states the rule of three first and so does refuse()."""
    both = Candidate(unit="u", path="src/app/headers.py", module_lines=1, consumers=1)
    assert refuse(both) == "rule_of_three"


# --- what cannot be placed is refused, not passed --------------------------


@pytest.mark.parametrize("path", ["", "   "])
def test_a_candidate_without_a_path_is_refused_rather_than_guessed_at(path: str) -> None:
    assert refuse(Candidate(**{**vars(GOOD), "path": path})) == "out_of_scope"


@pytest.mark.parametrize("path", ["/", "//", "./"])
def test_a_path_that_is_only_separators_names_no_file_and_is_refused(path: str) -> None:
    """It survives the empty-string guard and still points at nothing."""
    assert refuse(Candidate(**{**vars(GOOD), "path": path})) == "out_of_scope"


def test_a_candidate_without_a_unit_is_refused() -> None:
    assert refuse(Candidate(**{**vars(GOOD), "unit": ""})) == "out_of_scope"


def test_the_defaults_refuse_because_an_unfilled_field_is_not_evidence() -> None:
    """A caller that could not count consumers gets a refusal, not a move."""
    assert refuse(Candidate(unit="u", path="src/app/headers.py")) == "rule_of_three"


# --- the report ------------------------------------------------------------


def test_triage_splits_and_names_the_rule_for_each_refusal() -> None:
    report = triage([
        GOOD,
        Candidate(**{**vars(GOOD), "unit": "small", "module_lines": 10}),
        Candidate(**{**vars(GOOD), "unit": "thin", "consumers": 1}),
    ])
    assert [c.unit for c in report.accepted] == ["parse_header"]
    assert {(r.candidate.unit, r.rule) for r in report.refused} == {
        ("small", "size_floor"),
        ("thin", "rule_of_three"),
    }
    assert report.found == 3


def test_a_run_with_only_refusals_reports_no_moves_and_no_cost() -> None:
    report = triage([Candidate(unit="u", path="scripts/x.py", module_lines=900, consumers=9)])
    assert report.accepted == []
    assert report.moved == [] and report.reverted == [] and report.cost_usd == 0.0
    assert report.to_dict()["refused"] == [{"unit": "u", "path": "scripts/x.py",
                                            "rule": "out_of_scope"}]


def test_triage_of_nothing_is_a_report_not_a_failure() -> None:
    assert triage([]).to_dict()["found"] == 0


def test_a_phase_that_could_not_look_says_so_instead_of_reporting_zero() -> None:
    """`found: 0` and "no index" are different facts and must not render alike."""
    report = skipped("livespec is not mounted")
    assert report.skipped == "livespec is not mounted"
    assert report.found == 0
    assert triage([]).skipped == ""


def test_skipped_without_a_reason_still_states_one() -> None:
    assert skipped("").skipped
