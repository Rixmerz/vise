"""The runtime's data, its serialisation, and the two places a brief has to be
readable rather than merely present.

The brief tests are not cosmetic. A brief that carries the previous attempt's
classification in a field nobody renders is a brief that does not carry it: the
worker reads text.
"""
from __future__ import annotations

import json

import pytest

from vise.runtime.artifacts import ArtifactError, ArtifactStore
from vise.runtime.budget import BudgetLedger
from vise.runtime.contracts import (
    REPLAN_KINDS,
    RETRY_KINDS,
    TERMINAL_STATES,
    Artifact,
    Attempt,
    Criticality,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskBrief,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)


def test_usage_adds_componentwise():
    total = Usage(10, 20, 0.5, 1.5) + Usage(1, 2, 0.25, 0.5)
    assert (total.tokens_in, total.tokens_out) == (11, 22)
    assert total.cost_usd == pytest.approx(0.75)
    assert total.wall_time_s == pytest.approx(2.0)


def test_failure_kind_partitions_are_disjoint():
    """Retry and replan must never claim the same kind — they are opposite moves."""
    assert not (REPLAN_KINDS & RETRY_KINDS)


def test_terminal_states_exclude_waiting_human():
    """WAITING_HUMAN is not terminal: a person can still unblock it."""
    assert TaskState.WAITING_HUMAN not in TERMINAL_STATES
    assert TaskState.SUCCEEDED in TERMINAL_STATES


def test_brief_renders_prior_attempts_with_classification():
    brief = TaskBrief(
        run_id="r1", task_id="t1", name="JWT middleware", role="backend",
        attempts=(
            Attempt(1, "sonnet", "medium", Verdict.FAIL, "accepted a non-ASCII digit",
                    FailureKind.CODE_BUG),
        ),
    )
    text = brief.render()
    assert "already tried, do not repeat" in text
    assert "attempt 1 [sonnet/medium, code_bug] accepted a non-ASCII digit" in text


def test_brief_says_so_when_no_acceptance_criteria_exist():
    """A task nobody can verify must say that in the brief, not stay silent."""
    text = TaskBrief(run_id="r", task_id="t", name="n", role="backend").render()
    assert "never verified" in text


def test_brief_flags_undeclared_ownership_for_a_writing_task():
    text = TaskBrief(run_id="r", task_id="t", name="n", role="backend", writes=True).render()
    assert "ownership: undeclared" in text


def test_brief_omits_the_ownership_warning_for_a_read_only_task():
    text = TaskBrief(run_id="r", task_id="t", name="n", role="review", writes=False).render()
    assert "ownership" not in text


def test_brief_renders_criticality_only_when_it_is_not_routine():
    routine = TaskBrief(run_id="r", task_id="t", name="n", role="backend").render()
    critical = TaskBrief(
        run_id="r", task_id="t", name="n", role="backend",
        criticality=Criticality.CRITICAL,
    ).render()
    assert "criticality" not in routine
    assert "criticality: critical" in critical


def test_result_folds_into_an_attempt_preserving_classification():
    result = TaskResult(
        task_id="t1", verdict=Verdict.FAIL, summary="wrong branch",
        classification=FailureKind.TEST_BUG, model="sonnet", effort="high",
        usage=Usage(1, 2, 0.3, 4.0),
    )
    attempt = result.to_attempt(2)
    assert attempt.number == 2
    assert attempt.classification is FailureKind.TEST_BUG
    assert attempt.usage.cost_usd == pytest.approx(0.3)
    assert "sonnet/high" in attempt.render()


def test_result_and_spec_serialise_to_json():
    result = TaskResult(
        task_id="t", verdict=Verdict.PASS,
        artifacts=(Artifact("r", "t", "review", {"findings": []}),),
    )
    spec = RunSpec(run_id="r", goal="g", project_dir="/tmp/x", budget=RunBudget(max_cost_usd=5))
    assert json.loads(json.dumps(result.to_dict()))["verdict"] == "pass"
    assert json.loads(json.dumps(spec.to_dict()))["budget"]["max_cost_usd"] == 5


def test_artifact_round_trips_through_a_dict():
    art = Artifact("run", "task", "research", {"k": [1, 2]})
    assert Artifact.from_dict(art.to_dict()).payload == {"k": [1, 2]}


def test_artifact_from_dict_repairs_a_non_mapping_payload():
    assert Artifact.from_dict({"payload": "not a dict"}).payload == {}


# --- budget ---------------------------------------------------------------


def test_ledger_reports_unset_ceilings_rather_than_calling_them_unlimited():
    report = BudgetLedger(RunBudget()).report()
    assert set(report["unset_ceilings"]) == {"max_cost_usd", "max_workers", "max_wall_time_s"}
    assert report["remaining_usd"] is None


def test_ledger_stops_the_run_when_a_task_would_exceed_the_cost_ceiling():
    ledger = BudgetLedger(RunBudget(max_cost_usd=1.0))
    ledger.spend("t1", Usage(cost_usd=0.9))
    admission = ledger.admit(0.5)
    assert not admission
    assert admission.stop is True
    assert "remaining" in admission.reason


def test_ledger_defers_rather_than_stops_when_only_parallelism_is_saturated():
    ledger = BudgetLedger(RunBudget(max_parallel=2))
    admission = ledger.admit(0.1, in_flight=2)
    assert not admission
    assert admission.stop is False


def test_cost_exhaustion_outranks_saturation_in_the_reported_reason():
    """A saturated scheduler on a dead budget must say 'out of budget'."""
    ledger = BudgetLedger(RunBudget(max_cost_usd=1.0, max_parallel=1))
    ledger.spend("t", Usage(cost_usd=1.0))
    assert ledger.exhausted()
    assert ledger.admit(0.5, in_flight=5).stop is True


def test_ledger_accumulates_per_task_across_attempts():
    ledger = BudgetLedger(RunBudget())
    ledger.spend("t1", Usage(cost_usd=0.2))
    ledger.spend("t1", Usage(cost_usd=0.3))
    assert ledger.report()["by_task"]["t1"]["cost_usd"] == pytest.approx(0.5)


# --- artifacts ------------------------------------------------------------


def test_store_writes_reads_and_supersedes(tmp_path):
    store = ArtifactStore(tmp_path, "run-1")
    store.put(Artifact("run-1", "t1", "research", {"v": 1}))
    store.put(Artifact("run-1", "t1", "research", {"v": 2}))
    assert store.get("t1", "research").payload == {"v": 2}


def test_store_gathers_inputs_from_dependencies(tmp_path):
    store = ArtifactStore(tmp_path, "run-1")
    store.put(Artifact("run-1", "a", "research", {"v": 1}))
    store.put(Artifact("run-1", "b", "plan", {"v": 2}))
    store.put(Artifact("run-1", "c", "review", {"v": 3}))
    kinds = {a.kind for a in store.inputs_for(["a", "b"])}
    assert kinds == {"research", "plan"}


def test_store_returns_nothing_for_a_task_that_produced_nothing(tmp_path):
    assert ArtifactStore(tmp_path, "r").for_task("never-ran") == ()


def test_unreadable_artifact_raises_rather_than_reading_as_absent(tmp_path):
    """Absent means upstream produced nothing. Unreadable means we lost it."""
    store = ArtifactStore(tmp_path, "run-1")
    path = store.put(Artifact("run-1", "t1", "plan", {"v": 1}))
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ArtifactError):
        store.get("t1", "plan")


def test_store_neutralises_path_traversal_in_a_task_id(tmp_path):
    """A task id is data. It must not be able to address a path outside the run."""
    store = ArtifactStore(tmp_path, "run")
    written = store.put(Artifact("run", "../../escape", "plan", {}))
    assert store.root.resolve() in written.resolve().parents


def test_store_refuses_an_empty_path_component(tmp_path):
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path, "run").put(Artifact("run", "   ", "plan", {}))


def test_a_read_only_task_is_never_told_what_it_may_write():
    brief = TaskBrief(
        run_id="r", task_id="t", name="n", role="review",
        writes=False, ownership=("src/**",),
    )
    assert "you may write" not in brief.render()


def test_wall_time_ceiling_prefers_real_elapsed_over_aggregate_worker_time():
    """With parallelism, summed worker time exceeds the time that passed."""
    ledger = BudgetLedger(RunBudget(max_wall_time_s=100))
    ledger.spend("a", Usage(wall_time_s=60))
    ledger.spend("b", Usage(wall_time_s=60))
    assert ledger.admit(0.0).stop is True, "aggregate is the conservative fallback"
    assert ledger.admit(0.0, elapsed_s=61.0).ok, "61s of real time is under the ceiling"


def test_the_wall_time_reason_names_which_measure_it_used():
    ledger = BudgetLedger(RunBudget(max_wall_time_s=10))
    ledger.spend("a", Usage(wall_time_s=99))
    assert "aggregate worker time" in ledger.admit(0.0).reason
    assert "elapsed" in ledger.admit(0.0, elapsed_s=99).reason
