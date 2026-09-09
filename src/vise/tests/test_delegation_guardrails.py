"""Rules added after watching subagents fail on a real migration.

A field report from a Python-to-Node port recorded thirteen failures across six
parallel agents. The useful finding was not the failure list — it was that most
of the mitigations already existed in this repository, in a file the agent that
needed them does not load.

`search_similar` before writing a helper is described in `orchestration` as
"the one that pays for itself". `orchestration` is loaded by the coordinator,
who is not the one writing helpers; no charter preloads `codelayer`. The
mutation procedure — invert, run, confirm red, restore — is in `tester`'s
charter only, so a builder writing a test as part of an implementation task got
the belief ("a test never observed failing has not been shown to test
anything") without the action.

The fix in both cases was to move the rule to `engineering-baseline`, which all
22 charters preload. These tests pin it there, and pin the parts of each rule
that the report showed were the parts that actually did the work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BASELINE = REPO / "skills" / "engineering-baseline" / "SKILL.md"
ORCHESTRATION = REPO / "skills" / "orchestration" / "SKILL.md"
TYPESCRIPT = REPO / "skills" / "typescript-rules" / "SKILL.md"
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


# --- the duplicate a builder is about to write -----------------------------

def test_the_baseline_says_why_the_search_came_back_empty(baseline: str):
    """The whole rule turns on this: the copy is never named like the
    original, so grepping your own name for it proves nothing. A rule that
    only says "don't duplicate" was in the brief, verbatim, three sentences
    long, and the agent duplicated anyway."""
    assert "the duplicate has a different name" in baseline
    assert "That silence is not evidence." in baseline


def test_the_baseline_names_the_third_exit(baseline: str):
    """"Don't edit another agent's file" and "don't duplicate" leave no legal
    move when the function exists but is not exported. Without a third exit
    the agent duplicates — six non-equivalent copies of one function, in the
    reported case. Naming the exit turned those into one-word changes."""
    assert "pending splice" in baseline
    assert "is not exported" in baseline
    assert "a file you do not own" in baseline


def test_a_copy_may_not_be_filed_as_a_deferral(baseline: str):
    """The reported violation arrived as a `ponytail:` note in the agent's own
    report — the duplication rationalised in the vocabulary of good practice,
    which is harder to catch than an unexplained copy."""
    assert "**A copy is not a deferral.**" in baseline
    assert "If you copied anyway, say you copied." in baseline


# --- tests that pass with the code broken ----------------------------------

def test_the_mutation_procedure_reaches_more_than_the_tester(baseline: str):
    """`tester` has carried the procedure all along. Both tests that lied were
    written by agents that are not `tester`."""
    for step in ("invert its assertion", "confirm red", "restore"):
        assert step in baseline, f"baseline never says {step!r}"
    tester = _collapsed(TESTER)
    assert "invert its assertion" in tester, "the charter lost its copy"


def test_a_green_mutation_is_a_finding(baseline: str):
    """The one that caught the event-loop bug. The agent mutated, saw green,
    and investigated instead of reporting a pass — three tasks had reached
    their first await synchronously, so the queueing branch never ran and the
    mutated line was never executed."""
    assert "**A mutation that stays green is a finding, not a pass.**" in baseline


def test_the_indistinguishable_value_trap_is_named(baseline: str):
    """Sixteen tests passed with the splice undone because the broken default
    and the real loader both returned `{}` without a database. Comparing
    results cannot discriminate when both paths produce the same value."""
    assert "comparing results discriminates nothing" in baseline
    assert "Assert on the **call**" in baseline


def test_no_flag_may_manufacture_a_clean_run(baseline: str):
    """Stated language-agnostically in the baseline, because the flag differs
    per runner and the reasoning does not."""
    assert "Never add a flag to make the suite pass or exit." in baseline


# --- the linter ------------------------------------------------------------

def test_lint_zero_never_widens_the_public_api(baseline: str):
    """Six dead functions were exported to silence an unused-symbol warning,
    which announces them as API — worse than the warning it removed."""
    assert "never bought by widening the public API" in baseline


# --- what the coordinator owes the agent -----------------------------------

def test_the_brief_must_cite_paths_that_resolve(orchestration: str):
    """This one is the cost of the rule directly above it: telling briefs to
    cite `path:line` instead of pasting makes an unresolvable path the new
    failure. A brief pointed into a worktree at a file written in the main
    checkout, and the agent stopped without writing a line."""
    assert "`ls` every path the brief cites" in orchestration
    assert "worktree" in orchestration


def test_report_shaped_work_is_written_as_it_goes(orchestration: str):
    """Two agents died before delivering, and there is no record of what they
    had found. A file on disk survives the agent."""
    assert "write it to disk as it goes" in orchestration


# --- the language-specific halves ------------------------------------------

def test_typescript_rules_name_the_module_load_check():
    """A module whose ESM import fails does not load at all, yet its test
    passed — the test mocked that import, so it never loaded the real module.
    Only an import audit found it."""
    text = _collapsed(TYPESCRIPT)
    assert "Don't take a green test as proof the module loads." in text
    assert "--forceExit" in text


def test_typescript_rules_keep_security_last():
    """House rule: security outranks style, and the reader should reach it
    after the style rules. Pinned here because this change edited the file."""
    headings = [
        line for line in TYPESCRIPT.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    assert headings[-1].startswith("## Security"), headings
