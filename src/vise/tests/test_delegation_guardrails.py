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


# --- the gate that never ran ------------------------------------------------

def test_orchestration_checks_the_profile_before_dispatching(orchestration: str):
    """Nothing in this repository routed to `/bootstrap`: zero mentions in
    `orchestration`, in the workflow suggester hook, or in any bundled
    workflow. Without a profile `tests_pass` falls back to `pytest -q` and
    returns `passed=True` / `outcome="unverified"` on a repo whose suite is
    Jest — a gate that reads green and never ran. The reported migration was
    exactly such a repo."""
    assert "`/bootstrap` first" in orchestration
    assert "the difference between a gate and a decoration" in orchestration
    assert "outcome=\"unverified\"" in orchestration


def test_a_cloned_profile_is_not_an_approved_one(orchestration: str):
    """Presence is not approval — the same absent/unreadable distinction the
    rest of vise runs on."""
    assert "was never approved on this machine" in orchestration


# ===========================================================================
# Second field report — an Electron desktop app with its own API, migrated and
# restyled: 26 incidents across 11 subagents in five waves.
#
# Four of the thirteen causes already had mitigations from the previous round
# and behaved as intended: an agent quoted "the same value discriminates
# nothing" while fixing an empty test, another discarded an invalid mutation on
# its own, a third reported a `pending splice` rather than exporting from a file
# it did not own, and findings written to disk survived an agent that died with
# its session. What follows pins what THAT round did not cover.
# ===========================================================================

# --- case 5.1: an acceptance criterion green for not having looked ---------

def test_the_baseline_makes_the_runner_prove_it_saw_the_new_code(baseline: str):
    """Seventeen tests outside the runner's `include` globs never ran and the
    suite stayed green; the root `tsconfig.json` did not list the new package
    and `tsc --noEmit` passed without reading it. An absent test is not a
    failing test, and the difference does not show in the colour — it shows in
    the count."""
    assert "New code is not covered until you have seen the runner count it" in baseline
    assert "Note the test count before and after" in baseline
    assert "root `tsconfig.json` passes `--noEmit` without being read" in baseline


def test_the_orchestrator_owns_the_shared_config_files(orchestration: str):
    """The other half: neither file belonged to anyone. The agent fixed them on
    its own initiative and flagged it, which is not something to rely on."""
    assert "Config files that describe the whole repo need an owner" in orchestration
    assert "no agent touches and no gate misses" in orchestration


# --- case 1.1: the direction of the money, inverted in the brief -----------

def test_a_directional_claim_must_cite_the_line_that_establishes_it(orchestration: str):
    """The worst incident in the report. The design document stated a filter's
    direction backwards — adding an entry to a list was described as including
    it where the code excludes it. The agent derived the interface copy from
    that faithfully and the result was inverted in both directions, on the
    screen a person reads immediately before acting. No test could catch it:
    the code was right and the prose was what lied."""
    assert "Verify every directional claim against the code" in orchestration
    assert "the code is correct and the prose is what lies" in orchestration
    assert "does not produce a wrong agent, it produces a wrong product" in orchestration


# --- cases 2.1/2.2/2.3: the contract guessed rather than read --------------

def test_a_contract_is_quoted_not_paraphrased(orchestration: str):
    """The same mistake three times: the brief described from memory what
    another agent was building. A field remembered under a shorter name than
    the code returns, an unmentioned required parameter answering 400, and a
    type shape that turned out to be a different state machine."""
    assert "A contract between two agents is quoted, never paraphrased" in orchestration
    assert "path:line" in orchestration
    assert "two agents building faithfully against two different texts" in orchestration


# --- cases 4.1/4.2: partition by file vs. coupling by type -----------------

def test_the_type_set_is_resolved_as_well_as_the_caller_set(orchestration: str):
    """Adding two members to a union changes no signature, so the caller-set
    pass never fires — and it broke an exhaustive `Record` in a file the agent
    did not own, plus a validation schema that then answered 400 to every row
    carrying a value nobody had told it about."""
    assert "the type set" in orchestration
    assert "changes no signature, so the pass above never fires" in orchestration
    assert "400s every row carrying a value nobody told it about" in orchestration


# --- case 6.2: a comment about a file the author does not own --------------

def test_a_comment_may_not_describe_a_file_you_do_not_own(baseline: str):
    """True when written and false when it landed: the other agent added the
    very join the comment said was absent. In parallel work that expires with
    nothing to announce it."""
    assert "Never describe the current state of a file you do not own" in baseline
    assert "lands in someone else's diff" in baseline


# --- case 10.1: the CLI that installed packages nobody asked for -----------

def test_the_baseline_makes_you_diff_the_manifest_after_a_generator(baseline: str):
    """A component CLI wrote an import of a bare module name across 16 files and
    added the unrelated npm package of that name to `dependencies` — it is not
    the project's alias — plus two more nobody asked for."""
    assert "diff the manifest and account for every dependency it added" in baseline
    assert "typosquat of the alias you meant" in baseline
    assert "CWE-1357" in baseline


# --- case 11.1: an interactive CLI with no TTY ----------------------------

def test_the_baseline_names_the_prompt_that_hangs(baseline: str):
    """A migration generator asks whether a column is new or renamed. With no
    TTY it waits forever, and piping into it does not help, because the prompt
    reads the terminal rather than stdin."""
    assert "Running a command that can ask you a question" in baseline
    assert "a prompt reads the terminal, not stdin" in baseline
    assert "drive it with `expect`" in baseline


def test_a_credential_prompt_is_a_stop_not_an_automation_target(baseline: str):
    """The corollary that stops the rule above from teaching the opposite."""
    assert "is a stop, not a puzzle to automate" in baseline


# --- case 12.2: an invalid mutation proves nothing -------------------------

def test_a_suite_that_did_not_run_is_neither_passing_nor_failing(baseline: str):
    """The mutation left a syntax error and the runner reported "no tests". The
    agent discarded it on its own — "that is not evidence of anything" — but the
    rule was not written down, and confusing the two turns mutation into
    theatre."""
    assert "A suite that did not run is neither passing nor failing" in baseline
    assert "Read the count, not the colour." in baseline
