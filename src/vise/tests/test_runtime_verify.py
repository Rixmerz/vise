"""A worker's pass becomes a success only when a different agent agrees.

The tests that matter most here are about what the verifier is *not* given: the
implementer's prompt and the implementer's account of what it did. A verifier
who reads the argument for why the code is right is reviewing the argument.
"""
from __future__ import annotations

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
    REVIEW_PROBES,
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
