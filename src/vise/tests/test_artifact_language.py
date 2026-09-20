"""The tree is a third channel, and nothing here used to say so.

A field report from a session conducted in one language, working on a
repository written in another: the artifacts came out in the session's
language — a comment here, a test name there, a commit subject — and the
repository ended up half in each.

Nothing had been violated. Both language rules this repository ships name a
channel: `orchestration` requires the brief to be English, `engineering-
baseline` requires the report to be English, and each one closes by carving out
the same exception, that the user is answered in the user's language. An agent
holding two rules of the form "this channel is English, the user channel is
not", and nothing at all about the tree, resolves the third case by analogy
with whichever of the two it read last. Half the time that is the wrong one,
and the trace it leaves outlives the run.

The fix is the rule that was missing rather than a tightening of the two that
were there: what you commit takes the *repository's* language. These tests pin
it to `engineering-baseline`, which all 22 charters preload, pin the two
sentences that do the work — read it off the files, and never translate what
you were not asked to translate — and pin the cross-reference, because either
half surviving alone restores exactly the gap that produced the report.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BASELINE = REPO / "skills" / "engineering-baseline" / "SKILL.md"
ORCHESTRATION = REPO / "skills" / "orchestration" / "SKILL.md"
TESTER = REPO / "agents" / "tester.md"


def _collapsed(path: Path) -> str:
    # Collapsed, because a rule that wraps across a line is the same rule and
    # an assertion that breaks on reflowed prose teaches people to delete it.
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.fixture(scope="module")
def baseline() -> str:
    return _collapsed(BASELINE)


@pytest.fixture(scope="module")
def orchestration() -> str:
    return _collapsed(ORCHESTRATION)


def test_the_baseline_names_the_tree_as_its_own_channel(baseline: str):
    """The rule has to exist where every charter already looks. Moved to a
    skill only the coordinator loads, it reaches nobody who writes a file."""
    assert "### Natural language — the repository's, not the session's" in (
        BASELINE.read_text(encoding="utf-8")
    )
    assert (
        "What you commit takes the repository's language, not the session's."
        in baseline
    )


def test_the_baseline_says_where_to_read_the_language_off(baseline: str):
    """"Use the repository's language" is not actionable on its own — the
    agent has to be told what counts as the answer. Files it is already
    editing and recent commit subjects are both free to check and both
    already in front of it."""
    assert "Read the language off the files, not off the request." in baseline
    assert "the last twenty commit subjects" in baseline


def test_the_baseline_forbids_the_unrequested_translation(baseline: str):
    """The other direction of the same defect, and the more expensive one: an
    agent that decides the repository is in the wrong language and fixes it
    ships a diff nobody can review over a change nobody can find."""
    assert "Never translate a file you were not asked to translate." in baseline
    assert "that is a finding for your report" in baseline


def test_the_report_rule_points_at_the_repository_rule(baseline: str):
    """The pairing is the fix. "Write the report in English" standing alone is
    what got read as "write everything in English"; the repository rule
    standing alone leaves the report unscoped the same way."""
    assert (
        "so is the tree. What you commit follows the repository's language, "
        "under *Natural language*." in baseline
    )


def test_the_brief_rule_does_not_leave_the_tree_unscoped(orchestration: str):
    """`orchestration` states the brief's own language rule and is where a
    coordinator would look for the wave's. It must not stop at the two
    channels that carved out the exception."""
    assert (
        "what the wave writes into the repository takes the repository's language"
        in orchestration
    )
    assert "`engineering-baseline` carries the rule" in orchestration


def test_the_tester_does_not_pin_whole_messages():
    """The drift surfaced as red tests, which is the good case — but the tests
    were red because they had pasted whole sentences, so the repair on offer
    was pasting the new sentence in. A test that asserts the condition a
    message names survives the rewording that carries no behavior."""
    charter = _collapsed(TESTER)
    assert "DON'T assert a whole human-readable message." in charter
    assert "the substring naming the condition" in charter
