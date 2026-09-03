"""A run leaves what it learned in the project's memory.

Before this, the only path from a run into ``experience_memory`` was a commit
the person made afterwards. The replan reason — the most specific thing a run
can say about a repository — was in ``state.json`` and nowhere the next plan
would look.
"""
from __future__ import annotations

from vise.engines.experience_memory import VALID_TYPES, get_project_experience_store
from vise.runtime.contracts import (
    Attempt,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Verdict,
)
from vise.runtime.lessons import lessons_from, record_run_lessons
from vise.runtime.state import RunState


def _state(tmp_path) -> RunState:
    spec = RunSpec(
        run_id="r1", goal="ship the thing", project_dir=str(tmp_path),
        graph_name="feature-dev", node_id="implement", budget=RunBudget(),
    )
    return RunState(spec=spec)


def test_the_new_types_are_valid_memory_types():
    assert {"run_replanned", "run_blocked"} <= VALID_TYPES


def test_a_quiet_run_leaves_nothing(tmp_path):
    state = _state(tmp_path)
    state.emit("run_started", goal="g", tasks=1)
    state.emit("dispatched", task="a")

    assert lessons_from(state) == []
    assert record_run_lessons(state, str(tmp_path)) == 0


def test_a_replan_is_recorded_with_the_failed_attempts_reason(tmp_path):
    state = _state(tmp_path)
    record = state.record("a")
    record.result = TaskResult(
        task_id="a", verdict=Verdict.FAIL,
        summary="the spec wants a field the schema forbids",
        classification=FailureKind.SPEC_BUG,
    )
    record.state = TaskState.FAILED
    state.replans = 1
    state.emit("replanned", tasks=2, replans=1)

    [lesson] = lessons_from(state)

    assert lesson.type == "run_replanned"
    assert lesson.severity == "high"
    assert lesson.scope == "project"
    assert lesson.file_pattern == "run:feature-dev:implement"
    assert "ship the thing" in lesson.description
    assert "a (spec_bug): the spec wants a field the schema forbids" in lesson.resolution


def test_the_reason_falls_back_to_the_last_attempt(tmp_path):
    state = _state(tmp_path)
    record = state.record("a")
    record.attempts.append(Attempt(
        number=1, model="m", effort="e", verdict=Verdict.FAIL,
        summary="wrong question", classification=FailureKind.ARCHITECTURE_BUG,
    ))
    state.emit("replanned", tasks=2, replans=1)

    [lesson] = lessons_from(state)
    assert "a (architecture_bug): wrong question" in lesson.resolution


def test_a_run_parked_for_a_person_is_a_lesson(tmp_path):
    state = _state(tmp_path)
    state.emit("human_gate", task="b", reason="ownership overlaps src/auth")
    state.emit("unroutable", task="c", reason="role frobnicate")
    state.emit("drain_failed", task="d", future_kind="verify", error="boom")

    lessons = lessons_from(state)

    assert [x.type for x in lessons] == ["run_blocked"] * 3
    assert lessons[0].severity == "high"
    assert "task b: ownership overlaps src/auth" in lessons[0].description
    assert "task c: role frobnicate" in lessons[1].description
    assert "task d: verify" in lessons[2].description


def test_the_same_lesson_twice_in_one_run_is_recorded_once(tmp_path):
    state = _state(tmp_path)
    state.emit("unroutable", task="c", reason="role x")
    state.emit("unroutable", task="c", reason="role x")

    assert len(lessons_from(state)) == 1


def test_lessons_reach_the_project_store(tmp_path):
    state = _state(tmp_path)
    state.emit("human_gate", task="b", reason="a person must decide")

    assert record_run_lessons(state, str(tmp_path)) == 1

    store = get_project_experience_store(str(tmp_path))
    kinds = {e.type for e in store.entries}
    assert "run_blocked" in kinds
    [entry] = [e for e in store.entries if e.type == "run_blocked"]
    assert "a person must decide" in entry.description


def test_a_memory_that_cannot_be_written_does_not_fail_the_run(tmp_path, monkeypatch):
    import vise.runtime.lessons as mod

    def boom(_project_dir):
        raise OSError("disk is a lie")

    monkeypatch.setattr(mod, "get_project_experience_store", boom)
    state = _state(tmp_path)
    state.emit("human_gate", task="b", reason="r")

    assert record_run_lessons(state, str(tmp_path)) == 0


def test_a_lesson_whose_whole_content_is_a_dash_is_not_written(tmp_path):
    """A memory entry is retrieved and shown to a future agent like any other.

    `replan_unavailable in run r1 — ` reads as a truncated record rather than
    as an event with no detail, and the agent that reads it cannot tell which.
    Found by running `vise runtime compose` against a run recorded before the
    scheduler carried a reason on this event.
    """
    state = _state(tmp_path)
    state.emit("replan_unavailable")
    lesson = next(e for e in lessons_from(state) if "replan_unavailable" in e.description)
    assert lesson.description == "replan_unavailable in run r1"


def test_a_blocking_event_that_has_a_detail_still_carries_it(tmp_path):
    state = _state(tmp_path)
    state.emit("replan_unavailable", task="parser", reason="no replanner is configured")
    lesson = next(e for e in lessons_from(state) if "replan_unavailable" in e.description)
    assert lesson.description == (
        "replan_unavailable in run r1 — task parser: no replanner is configured"
    )
