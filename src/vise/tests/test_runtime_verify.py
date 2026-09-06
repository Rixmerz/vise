"""A worker's pass becomes a success only when a different agent agrees.

The tests that matter most here are about what the verifier is *not* given: the
implementer's prompt and the implementer's account of what it did. A verifier
who reads the argument for why the code is right is reviewing the argument.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from vise.engines.graph_engine import Task
from vise.runtime.artifacts import ArtifactStore
from vise.runtime.contracts import (
    Artifact,
    Attempt,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskBrief,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.verify import (
    LENSES,
    REVIEW_PROBES,
    VERIFY_INSTRUCTIONS,
    Verification,
    debugger_brief,
    parse_classification,
    parse_verification,
    render_verification,
    reviewer_brief,
    verification_artifact,
    verifier_brief,
)
from vise.runtime.worker import MockWorker


def _work_brief(**kw) -> TaskBrief:
    base = dict(
        run_id="r1", task_id="auth", name="JWT middleware", role="backend",
        prompt="SECRET IMPLEMENTER INSTRUCTIONS",
        acceptance=("an expired token is rejected with 401",),
        ownership=("src/auth/**",),
    )
    base.update(kw)
    return TaskBrief(**base)


def _work_result(**kw) -> TaskResult:
    base = dict(
        task_id="auth", verdict=Verdict.PASS,
        summary="TRUST ME THIS IS CORRECT",
        evidence="$ pytest tests/test_auth.py\n1 passed",
        checks="$ ruff\nok",
        changed_paths=("src/auth/token.py",),
    )
    base.update(kw)
    return TaskResult(**base)


# --- the verifier's brief -------------------------------------------------


def test_the_verifier_never_receives_the_implementers_prompt_or_summary():
    """The two artefacts a wrong-but-confident worker produces best."""
    text = verifier_brief(_work_brief(), _work_result()).render()
    assert "SECRET IMPLEMENTER INSTRUCTIONS" not in text
    assert "TRUST ME THIS IS CORRECT" not in text


def test_the_verifier_receives_the_criteria_the_diff_and_the_evidence():
    text = verifier_brief(_work_brief(), _work_result(), diff="- old\n+ new").render()
    assert "an expired token is rejected with 401" in text
    assert "src/auth/token.py" in text
    assert "1 passed" in text
    assert "+ new" in text


def test_the_verifier_is_read_only_and_holds_no_ownership():
    brief = verifier_brief(_work_brief(), _work_result())
    assert brief.writes is False
    assert brief.ownership == ()


def test_the_verifier_is_told_inconclusive_is_a_real_answer():
    assert "Inconclusive is a real" in verifier_brief(_work_brief(), _work_result()).render()


def test_the_verifiers_task_id_is_distinct_from_the_work_it_judges():
    assert verifier_brief(_work_brief(), _work_result()).task_id == "auth::verify"


# --- parsing --------------------------------------------------------------


def test_a_verification_artifact_wins_over_the_bare_verdict():
    result = TaskResult(
        task_id="auth::verify", verdict=Verdict.PASS,
        artifacts=(Artifact("r1", "auth", "verification",
                            {"verdict": "fail", "unmet": ["401 never asserted"]}),),
    )
    parsed = parse_verification(result)
    assert parsed.verdict is Verdict.FAIL
    assert parsed.unmet == ("401 never asserted",)


def test_an_unreadable_artifact_becomes_inconclusive_never_pass():
    """A verifier we cannot read has verified nothing. Defaulting to pass makes
    a broken verifier indistinguishable from one that always agrees."""
    result = TaskResult(
        task_id="v", verdict=Verdict.PASS,
        artifacts=(Artifact("r1", "auth", "verification", {"verdict": "probably?"}),),
    )
    assert parse_verification(result).verdict is Verdict.INCONCLUSIVE


def test_notes_filed_as_a_verification_artifact_do_not_shadow_the_verdict():
    """Found by running the scheduler against a real repo.

    ``verification`` is one of the artifact kinds RESULT_INSTRUCTIONS offers, so
    a verifier that files a test list under it is following the contract. Reading
    that as a failed verdict parse turned a live pass into inconclusive, blocked
    the task, and stalled the five tasks behind it — for a $0.39 run that
    produced correct, evidence-backed code and then deleted it.
    """
    result = TaskResult(
        task_id="v", verdict=Verdict.PASS, summary="every criterion met",
        artifacts=(Artifact("r1", "money", "verification",
                            {"result": "all passed", "tests_run": ["decimal", "mismatch"]}),),
    )
    parsed = parse_verification(result)
    assert parsed.verdict is Verdict.PASS


def test_a_later_artifact_still_supplies_the_verdict():
    """The notes need not come last. Only the first *readable* one decides."""
    result = TaskResult(
        task_id="v", verdict=Verdict.PASS,
        artifacts=(
            Artifact("r1", "money", "verification", {"tests_run": ["a"]}),
            Artifact("r1", "money", "verification", {"verdict": "fail", "unmet": ["no test"]}),
        ),
    )
    assert parse_verification(result).verdict is Verdict.FAIL


def test_without_an_artifact_the_bare_verdict_is_used():
    result = TaskResult(task_id="v", verdict=Verdict.FAIL, summary="no")
    assert parse_verification(result).verdict is Verdict.FAIL


def test_verification_serialises_and_renders():
    v = Verification(Verdict.FAIL, unmet=("401 never asserted",))
    assert v.to_dict()["verdict"] == "fail"
    assert "401 never asserted" in render_verification(v)
    assert verification_artifact("r1", "auth", v).kind == "verification"


def test_a_passing_verification_is_accepted():
    assert Verification(Verdict.PASS).accepted


# --- the reviewer ---------------------------------------------------------


def test_the_reviewer_is_asked_for_reasons_not_to_ship():
    text = reviewer_brief("r1", goal="add oauth").render()
    assert "should not ship" in text
    assert "never by an invented severity score" in text


def test_the_reviewer_probes_are_named_including_non_ascii():
    """The measured gap was the largest model missing non-ASCII input twice,
    because nothing told it to look."""
    text = reviewer_brief("r1", goal="g").render()
    assert any(probe.split(" —")[0] in text for probe in REVIEW_PROBES)
    assert "٥" in text


def test_the_reviewer_writes_nothing():
    assert reviewer_brief("r1", goal="g").writes is False


# --- the debugger ---------------------------------------------------------


def test_the_debugger_is_told_what_each_classification_costs():
    text = debugger_brief(_work_brief(), _work_result(verdict=Verdict.FAIL)).render()
    assert "trigger a replan" in text
    assert "retries at the same model" in text


def test_the_debugger_sees_previous_attempts():
    brief = _work_brief(attempts=(
        Attempt(1, "sonnet", "medium", Verdict.FAIL, "off by one", FailureKind.CODE_BUG),
    ))
    assert "off by one" in debugger_brief(brief, _work_result()).render()


@pytest.mark.parametrize("kind", list(FailureKind))
def test_every_classification_round_trips(kind):
    result = TaskResult(
        task_id="d", verdict=Verdict.PASS,
        artifacts=(Artifact("r", "d", "finding", {"classification": kind.value}),),
    )
    assert parse_classification(result) is kind


def test_an_unreadable_classification_is_none_rather_than_a_default():
    """Inventing CODE_BUG converts 'we do not know' into a decision with a cost."""
    result = TaskResult(task_id="d", verdict=Verdict.PASS, summary="hard to say")
    assert parse_classification(result) is None


# --- the scheduler integration -------------------------------------------


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="backend-python", role="backend", description="d", model="sonnet",
                  capabilities=("backend", "python")),
        AgentSpec(id="verifier", role="verify", description="d", model="sonnet",
                  effort="medium", writes=False, capabilities=("verify",)),
    ):
        reg.agents[spec.id] = spec
    return reg


class VerifyingWorker(MockWorker):
    """Passes the work; answers verification from a script."""

    def __init__(self, verdicts, **kw):
        super().__init__(**kw)
        self.verdicts = list(verdicts)

    def run(self, brief):
        if brief.role == "verify":
            verdict = self.verdicts.pop(0) if self.verdicts else Verdict.PASS
            payload = {"verdict": verdict.value}
            if verdict is Verdict.FAIL:
                payload["unmet"] = ["the 401 path is never exercised"]
            return TaskResult(
                task_id=brief.task_id, verdict=Verdict.PASS,
                artifacts=(Artifact(brief.run_id, brief.task_id, "verification", payload),),
                usage=Usage(cost_usd=0.85),
            )
        return super().run(brief)


def _run(tasks, worker, **kw):
    kw.setdefault("registry", _registry())
    spec = kw.pop("spec", RunSpec(run_id="r1", goal="g", project_dir="/nonexistent-not-a-repo",
                                  budget=RunBudget(max_parallel=2)))
    return Scheduler(worker=worker, **kw).run(spec, tasks)


def _task(**kw) -> Task:
    base = dict(id="auth", name="auth", role="backend", ownership=["src/auth/**"],
                acceptance=["an expired token is rejected with 401"])
    base.update(kw)
    return Task(**base)


def test_a_pass_the_verifier_agrees_with_succeeds():
    worker = VerifyingWorker([Verdict.PASS])
    state = _run([_task()], worker)
    assert state.tasks["auth"].state is TaskState.SUCCEEDED
    assert "verified" in state.tasks["auth"].note
    assert any(e["kind"] == "verified" for e in state.events)


def test_a_pass_the_verifier_rejects_escalates_with_the_verifiers_reasons():
    worker = VerifyingWorker([Verdict.FAIL, Verdict.PASS])
    state = _run([_task()], worker)
    assert state.tasks["auth"].state is TaskState.SUCCEEDED
    briefs = [b for b in worker.briefs if b.role == "backend"]
    assert len(briefs) == 2, "the rejected task was tried again"
    assert "the 401 path is never exercised" in briefs[1].render()
    assert briefs[1].model + "/" + briefs[1].effort == "sonnet/high"


def test_an_inconclusive_verification_blocks_rather_than_retrying_the_work():
    """Re-running the implementer cannot fix a verifier that would not run."""
    worker = VerifyingWorker([Verdict.INCONCLUSIVE])
    state = _run([_task()], worker)
    assert state.tasks["auth"].state is TaskState.BLOCKED
    assert "could not evaluate" in state.tasks["auth"].note
    assert len([b for b in worker.briefs if b.role == "backend"]) == 1


def test_a_verifier_that_raises_blocks_rather_than_failing_the_work():
    class Exploding(MockWorker):
        def run(self, brief):
            if brief.role == "verify":
                raise RuntimeError("verifier died")
            return super().run(brief)

    state = _run([_task()], Exploding())
    assert state.tasks["auth"].state is TaskState.BLOCKED


def test_a_task_with_no_acceptance_criteria_is_not_verified():
    """Nothing to check against; the brief already says it can never be verified."""
    worker = VerifyingWorker([])
    state = _run([_task(acceptance=[])], worker)
    assert state.tasks["auth"].state is TaskState.SUCCEEDED
    assert not [b for b in worker.briefs if b.role == "verify"]


def test_verification_can_be_switched_off():
    worker = VerifyingWorker([Verdict.FAIL])
    state = _run([_task()], worker, config=SchedulerConfig(verify=False))
    assert state.tasks["auth"].state is TaskState.SUCCEEDED
    assert not [b for b in worker.briefs if b.role == "verify"]


def test_verification_costs_are_charged_to_the_run():
    worker = VerifyingWorker([Verdict.PASS])
    state = _run([_task()], worker)
    assert state.ledger.spent.cost_usd >= 0.85
    assert "auth::verify" in state.ledger.by_task


def test_the_verification_is_stored_as_an_artifact(tmp_path):
    store = ArtifactStore(tmp_path, "r1")
    _run([_task()], VerifyingWorker([Verdict.PASS]), artifacts=store)
    stored = store.get("auth", "verification")
    assert stored is not None and stored.payload["verdict"] == "pass"


def test_a_failed_dependency_is_not_released_by_a_rejected_verification():
    worker = VerifyingWorker([Verdict.FAIL] * 8)
    tasks = [
        _task(),
        Task(id="next", name="next", role="backend", ownership=["src/next/**"],
             dependencies=["auth"]),
    ]
    state = _run(tasks, worker)
    assert state.tasks["next"].state is not TaskState.SUCCEEDED


# --- a panel: how many independent opinions SUCCEEDED needs -----------------


class PanelWorker(VerifyingWorker):
    """Answers each panel member from a script, and records their briefs."""

    def __init__(self, verdicts, **kw):
        super().__init__(verdicts, **kw)
        self.verify_briefs = []

    def run(self, brief):
        if brief.role == "verify":
            self.verify_briefs.append(brief)
        return super().run(brief)


def test_the_default_is_one_verifier_and_its_brief_is_unchanged():
    worker = PanelWorker([Verdict.PASS])
    state = _run([_task()], worker)
    assert len(worker.verify_briefs) == 1
    assert worker.verify_briefs[0].task_id == "auth::verify", "the id every reader knows"
    assert worker.verify_briefs[0].prompt == VERIFY_INSTRUCTIONS, "no lens appended"
    assert state.tasks["auth"].state is TaskState.SUCCEEDED


def test_a_panel_is_briefed_from_distinct_lenses_and_none_sees_another():
    worker = PanelWorker([Verdict.PASS] * 3)
    _run([_task(verifiers=3)], worker)

    briefs = worker.verify_briefs
    assert len(briefs) == 3
    assert len({b.task_id for b in briefs}) == 3, "each files under its own id"
    assert len({b.prompt for b in briefs}) == 3, "three questions, not one asked thrice"
    for brief in briefs:
        rendered = brief.render()
        assert "SECRET IMPLEMENTER INSTRUCTIONS" not in rendered
        assert "TRUST ME THIS IS CORRECT" not in rendered
        for other in briefs:
            if other is not brief:
                assert other.task_id not in rendered, "no member knows the others exist"


def test_a_majority_passes_and_every_verdict_is_kept(tmp_path):
    store = ArtifactStore(tmp_path, "r1")
    worker = PanelWorker([Verdict.PASS, Verdict.FAIL, Verdict.PASS])
    state = _run([_task(verifiers=3)], worker, artifacts=store)

    assert state.tasks["auth"].state is TaskState.SUCCEEDED
    assert "2 pass" in state.tasks["auth"].note and "1 fail" in state.tasks["auth"].note
    assert store.get("auth", "verification").payload["verdict"] == "pass", "the decision"
    kept = [store.get(f"auth::verify[{i}]", "verification") for i in (1, 2, 3)]
    assert [k.payload["verdict"] for k in kept] == ["pass", "fail", "pass"], (
        "the dissent survives — a panel that only recorded its conclusion "
        "would hide the one member who disagreed"
    )


def test_a_majority_fails_with_the_union_of_the_failing_reasons():
    """And only theirs. A passing member's notes in a failing panel would send
    the next attempt to fix what somebody thought was already right."""

    class _Chatty(PanelWorker):
        """Every member speaks in `reasons`. The failing ones carry no `unmet`
        on purpose: `unmet` outranks `reasons` in the retry's summary, so a
        passing member's note smuggled into `reasons` would be invisible behind
        it — which is how the first draft of this test passed with the rule
        removed."""

        def run(self, brief):
            result = super().run(brief)
            if brief.role != "verify" or not result.artifacts:
                return result
            payload = dict(result.artifacts[0].payload)
            payload.pop("unmet", None)
            payload["reasons"] = (
                ["MINOR NIT FROM A MEMBER WHO PASSED IT"] if payload["verdict"] == "pass"
                else ["the 401 path is never exercised"]
            )
            return replace(result, artifacts=(
                replace(result.artifacts[0], payload=payload),))

    worker = _Chatty([Verdict.FAIL, Verdict.FAIL, Verdict.PASS] + [Verdict.PASS] * 3)
    state = _run([_task(verifiers=3)], worker)

    rejected = [b for b in worker.briefs if b.role != "verify"][1]
    assert "the 401 path is never exercised" in rejected.render()
    assert "MINOR NIT" not in rejected.render(), (
        "the passing member's reasons did not reach the retry"
    )
    assert state.tasks["auth"].state is TaskState.SUCCEEDED, "the retry then passed"


def test_no_majority_either_way_blocks_because_they_did_not_decide():
    worker = PanelWorker([Verdict.PASS, Verdict.FAIL, Verdict.INCONCLUSIVE])
    state = _run([_task(verifiers=3)], worker)

    record = state.tasks["auth"]
    assert record.state is TaskState.BLOCKED
    assert "did not agree" in record.note
    assert "1 pass" in record.note and "1 fail" in record.note


def test_a_panel_of_two_that_splits_blocks_rather_than_passing():
    """Half is not a majority, and a gate that cannot decide fails closed."""
    worker = PanelWorker([Verdict.PASS, Verdict.FAIL])
    assert _run([_task(verifiers=2)], worker).tasks["auth"].state is TaskState.BLOCKED


def test_the_decision_waits_for_the_whole_panel():
    """Deciding on the first answer would be the majority of whoever was fastest.

    The verdicts are chosen so the two behaviours differ: the first answer is a
    pass, and the panel is a majority fail. A panel that decided early would
    accept the work on one opinion.
    """
    worker = PanelWorker([Verdict.PASS, Verdict.FAIL, Verdict.FAIL] + [Verdict.PASS] * 3)
    state = _run([_task(verifiers=3)], worker)
    assert len(worker.verify_briefs) == 6, "the first panel, then the retry's"
    assert len([b for b in worker.briefs if b.role != "verify"]) == 2, (
        "the majority rejected the first attempt, so there was a second"
    )
    # One decision per panel, not one per answer. The task outcome alone does
    # not discriminate: a panel that decided on every arriving verdict reaches
    # the same end by a different and much noisier route.
    assert len([e for e in state.events if e["kind"] == "panel"]) == 2
    assert state.tasks["auth"].state is TaskState.SUCCEEDED


def test_every_member_of_a_panel_is_charged_to_the_run():
    worker = PanelWorker([Verdict.PASS] * 3)
    state = _run([_task(verifiers=3)], worker)
    charged = {k: v.cost_usd for k, v in state.ledger.by_task.items() if "verify" in k}
    assert len(charged) == 3, charged
    assert sum(charged.values()) == pytest.approx(0.85 * 3)


def test_a_panel_that_loses_a_member_to_a_crash_still_decides():
    class _Crashing(PanelWorker):
        def run(self, brief):
            if brief.role == "verify" and len(self.verify_briefs) == 1:
                self.verify_briefs.append(brief)
                raise RuntimeError("the verifier died")
            return super().run(brief)

    worker = _Crashing([Verdict.PASS, Verdict.PASS])
    state = _run([_task(verifiers=3)], worker)
    assert state.tasks["auth"].state is TaskState.SUCCEEDED, (
        "two passes out of three is still a majority"
    )


def test_the_panel_split_reaches_the_event_log():
    worker = PanelWorker([Verdict.PASS, Verdict.PASS, Verdict.FAIL])
    state = _run([_task(verifiers=3)], worker)
    panel = [e for e in state.events if e["kind"] == "panel"]
    assert len(panel) == 1 and panel[0]["verdict"] == "pass"
    assert "2 pass" in panel[0]["split"]
    lenses = {e.get("lens") for e in state.events if e["kind"] == "verifying"}
    assert lenses == {name for name, _ in LENSES[:3]}


def test_a_single_verifier_emits_no_panel_event_and_no_lens():
    """A majority of one is not a split, and a reader of an ordinary run should
    not have to learn what a lens is."""
    state = _run([_task()], PanelWorker([Verdict.PASS]))
    assert not [e for e in state.events if e["kind"] == "panel"]
    assert all("lens" not in e for e in state.events if e["kind"] == "verifying")


def test_a_task_with_no_criteria_gets_no_panel_however_many_it_asks_for():
    worker = PanelWorker([Verdict.PASS] * 3)
    state = _run([_task(acceptance=[], verifiers=3)], worker)
    assert not worker.verify_briefs
    assert state.tasks["auth"].state is TaskState.SUCCEEDED


def test_the_regression_lens_reaches_for_the_tool_that_answers_it():
    """Of the four lenses this is the one a tool answers better than a reader.

    "What does this change that nobody asked it to" is a question about callers
    the diff does not show — which is what `git_diff_impact` computes. The other
    three lenses are judgement and stay judgement.
    """
    from vise.core.neighbours import LIVESPEC_TOOLS
    from vise.runtime.verify import LENSES

    lenses = dict(LENSES)
    assert "git_diff_impact" in lenses["regression"]
    assert "git_diff_impact" in LIVESPEC_TOOLS, (
        "a lens that names a call the contract does not pin is a rename waiting "
        "to happen silently"
    )
    for name in ("criteria", "evidence", "adversary"):
        assert not any(tool in lenses[name] for tool in LIVESPEC_TOOLS), (
            f"the {name} lens reaches for a tool; it is a judgement question"
        )


def test_the_regression_lens_still_works_without_the_tool():
    """vise cannot see whether livespec is mounted, so every mention of it has
    to carry the branch. A lens that assumes the tool turns a verifier without
    it into one that reports inconclusive for the wrong reason."""
    from vise.runtime.verify import LENSES

    regression = dict(LENSES)["regression"]
    assert "if `git_diff_impact` is in your tool surface" in regression.lower()
    assert "without it" in regression.lower()
    assert "workspace" in regression, (
        "livespec requires it on every call; a lens that omits it teaches a "
        "call that raises"
    )
