"""The file whose job is to say what this repo verifies, verified.

`.vise/quality.yaml` is the profile vise asks every other repo to write, and it
carried a header asserting that everything in it "already runs in CI". Four of
its eight checks did not, and a fifth ran at a different threshold — so a
maintainer reading the one file that exists to state what a merge has passed
concluded that SAST, dependency scanning, secret scanning and OpenSpec
validation had run. None had.

That is the shape this file is against. Not "a comment went stale" — a comment
that made a specific, checkable claim about another file, and nothing checked it.

Every claim below is derived from both files rather than restated here, so the
test fails when either moves, not when someone forgets to update a list.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
PROFILE = REPO / ".vise" / "quality.yaml"
CI = REPO / ".github" / "workflows" / "ci.yml"

#: How the header classifies each check. The test reads this back out of the
#: header itself, so the header is the single source and this is only the shape.
_SECTIONS = ("gated in CI", "reported only", "not in CI")


def _profile_text() -> str:
    return PROFILE.read_text(encoding="utf-8")


def _declared_checks() -> set[str]:
    return set(yaml.safe_load(_profile_text()).get("checks") or {})


def _header_claims() -> dict[str, str]:
    """Parse the header's own classification: {check name: section}."""
    text = _profile_text().split("checks:", 1)[0]
    claims: dict[str, str] = {}
    section = None
    for line in text.splitlines():
        body = line.lstrip("#").strip()
        for name in _SECTIONS:
            if body.startswith(name):
                section = name
                body = body[len(name):].strip()
                break
        if section is None or not body:
            continue
        for token in re.findall(r"\b[a-z_]+\b", body.split("—")[0].split("(")[0]):
            if token in _declared_checks():
                claims[token] = section
    return claims


def _ci_text() -> str:
    return CI.read_text(encoding="utf-8")


def _ci_runs(binary: str) -> bool:
    """Does CI invoke this tool at all, in any step?"""
    return re.search(rf"\brun:.*\b{re.escape(binary)}\b", _ci_text()) is not None or any(
        binary in line for line in _ci_text().splitlines()
        if line.strip().startswith(("python -m", "- run:", "run:")) or "  " in line
    )


def _ci_gates(binary: str) -> bool:
    """Does CI *fail* on it — i.e. the step is not continue-on-error?"""
    steps = re.split(r"\n      - name: ", _ci_text())
    for step in steps:
        if binary not in step:
            continue
        if "continue-on-error: true" not in step:
            return True
    return False


#: The tool each check name actually shells out to, read off the profile.
def _binary_for(check: str) -> str | None:
    cmd = yaml.safe_load(_profile_text())["checks"][check]
    parts = cmd if isinstance(cmd, list) else str(cmd).split()
    flat = " ".join(str(p) for p in parts)
    for tool in ("ruff", "coverage", "pytest", "bandit", "pip_audit",
                 "detect_secrets", "openspec", "mypy"):
        if tool in flat:
            return tool
    return None


def test_the_header_classifies_every_declared_check():
    """A check the header does not mention is a claim nobody can check."""
    unclassified = sorted(_declared_checks() - set(_header_claims()))
    assert not unclassified, (
        f"these checks are declared and the header says nothing about whether "
        f"CI runs them: {unclassified}"
    )


def test_the_header_classifies_nothing_that_is_not_declared():
    stale = sorted(set(_header_claims()) - _declared_checks())
    assert not stale, f"the header names checks that no longer exist: {stale}"


@pytest.mark.parametrize("check", sorted(_declared_checks()))
def test_each_check_is_where_the_header_says_it_is(check):
    claim = _header_claims().get(check)
    assert claim, f"{check} is unclassified"
    binary = _binary_for(check)
    assert binary is not None, (
        f"`{check}` runs a tool this test does not know about. Add it to the "
        f"list in `_binary_for` — skipping here would leave the header's claim "
        f"about {check} unchecked, which is the thing this file exists to stop"
    )

    if claim == "gated in CI":
        assert _ci_gates(binary), (
            f"the header says `{check}` is gated in CI, but no failing CI step "
            f"runs `{binary}`. Either add the step or move the claim"
        )
    elif claim == "reported only":
        assert _ci_runs(binary), f"the header says CI reports `{check}`; it does not"
        assert not _ci_gates(binary), (
            f"the header says `{check}` only reports, but its CI step fails the "
            f"build — say it gates, or stop gating on it"
        )
    else:  # not in CI
        assert not _ci_gates(binary), (
            f"the header says `{check}` is not in CI, and a CI step gates on "
            f"`{binary}`. The header is the thing people read"
        )


def test_the_header_makes_no_blanket_claim_about_ci():
    """The exact sentence that was false, in the exact place it was false."""
    header = _profile_text().split("checks:", 1)[0]
    assert "already runs in CI" not in header, (
        "a blanket claim cannot be checked per check — say which ones"
    )
