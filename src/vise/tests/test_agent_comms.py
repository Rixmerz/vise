"""What the orchestrator and a subagent may say to each other, and in what.

Two rules live on this channel and they pull against each other. The
orchestration skill has always said **every prompt is self-contained**, which
is easy to read as *tell it everything* — and that reading is how a brief ends
up restating `engineering-baseline` to an agent that preloaded it, and pasting
a file the agent is about to open anyway. The counterweight — carry only what
the agent cannot get for itself — was never written down next to it.

Neither was the language. Nothing in this repository said which language the
channel runs in, while every charter, every rules skill and every validator
deny message the agent reads next is English.

These tests pin both, and pin the limit as hard as the rule: the compression
stops at the constraint and at the evidence. A brief the builder misreads costs
a whole wasted wave, which is worth more than every word it saved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ORCHESTRATION = REPO / "skills" / "orchestration" / "SKILL.md"
BASELINE = REPO / "skills" / "engineering-baseline" / "SKILL.md"
CHARTERS = sorted((REPO / "agents").glob("*.md"))


def _collapsed(text: str) -> str:
    # Collapsed, because a rule that wraps across a line is the same rule and
    # an assertion that breaks on reflowed prose teaches people to delete it.
    return " ".join(text.split())


@pytest.fixture(scope="module")
def brief() -> str:
    text = ORCHESTRATION.read_text(encoding="utf-8")
    start = text.index("## The brief")
    end = text.index("\n## ", start + 4)
    return _collapsed(text[start:end])


@pytest.fixture(scope="module")
def reporting() -> str:
    text = BASELINE.read_text(encoding="utf-8")
    start = text.index("### Reporting done")
    return _collapsed(text[start:])


def test_the_brief_is_english(brief: str):
    """The one fact a reader needs first, stated where the brief is written."""
    assert "**English**" in brief


def test_the_report_is_english(reporting: str):
    """Same rule, other direction. The subagent never loads `orchestration`,
    so stating it there only would reach half the channel."""
    assert "**Write the report in English**" in reporting


def test_the_user_channel_is_excluded_in_both_places(brief: str, reporting: str):
    """Without this carve-out the rule reads as "always answer in English",
    which is a worse bug than the one it fixes: it would have vise answering a
    Spanish-speaking user in English."""
    for section in (brief, reporting):
        assert "different channel" in section


def test_the_brief_names_what_to_leave_out(brief: str):
    """The three omissions are the whole token argument. A section that says
    "be brief" without naming them is advice, not a rule."""
    for omission in (
        "**Never restate a rule it preloads.**",
        "**Never paste a file's contents.**",
        "**Never narrate.**",
    ):
        assert omission in brief, f"the brief rule dropped {omission!r}"


def test_the_brief_reconciles_the_self_contained_hard_rule(brief: str):
    """The two rules contradict each other when read apart, so the new one
    quotes the old one and says which reading is meant. Drop this and the
    hard rule below is left pointing the other way."""
    assert "self-contained" in brief
    assert "needs nothing out of your window" in brief


def test_general_purpose_keeps_its_exception(brief: str):
    """`general-purpose` preloads nothing. "Never restate a rule it preloads"
    applied to that agent would strip the skill names out of the one brief
    that has to carry them."""
    assert "`general-purpose` is the exception" in brief


def test_the_cutting_stops_at_the_constraint(brief: str):
    """Where this rule is allowed to reach. A negation with words missing
    reads as its opposite, and the agent that guesses wrong writes a diff the
    orchestrator throws away."""
    assert "acceptance criterion" in brief
    assert "stay whole sentences" in brief
    assert "reads as its opposite" in brief
    assert "stays verbatim" in brief


def test_the_cutting_stops_at_the_evidence(reporting: str):
    """`engineering-baseline` already says "done" is a claim needing its
    command and result. A shortening rule that ate that would turn every
    report back into an assertion."""
    assert "stops at the evidence" in reporting
    assert "**actual output** stay verbatim" in reporting
    assert "a summarised error is not a result" in reporting


@pytest.mark.parametrize("charter", CHARTERS, ids=lambda p: p.stem)
def test_every_charter_preloads_the_baseline(charter: Path):
    """The report half of the rule ships inside `engineering-baseline`. It
    reaches an agent only if that agent loads it — so this is what makes the
    placement true rather than assumed."""
    assert "engineering-baseline" in charter.read_text(encoding="utf-8")


def test_there_are_charters_to_check():
    """A glob that silently matches nothing turns the test above green."""
    assert len(CHARTERS) == 22, f"expected 22 charters, found {len(CHARTERS)}"
