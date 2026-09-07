"""A run leaves what it learned in the project's memory.

Before this, the only path from a run into ``experience_memory`` was a commit
the person made afterwards. The replan reason — the most specific thing a run
can say about a repository — was in ``state.json`` and nowhere the next plan
would look.
"""
from __future__ import annotations

from vise.engines.experience_memory import (
    VALID_TYPES,
    get_experience_store,
    get_project_experience_store,
)
from vise.runtime.contracts import (
    Attempt,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
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
    assert {"run_replanned", "run_blocked", "run_succeeded"} <= VALID_TYPES


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
    assert lesson.scope == "global"
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
    assert "a (architecture_bug, tried m/e): wrong question" in lesson.resolution


def test_a_task_that_burned_the_ladder_records_what_it_tried(tmp_path):
    """The case the old filter dropped. Only SPEC_BUG and ARCHITECTURE_BUG
    contributed a line, so a task that spent all four rungs on one wrong answer
    left a lesson reading "replan #1" and an empty resolution: the memory knew a
    replan happened and nothing about what had already been tried."""
    state = _state(tmp_path)
    record = state.record("a")
    for number, (model, effort) in enumerate(
        [("haiku", ""), ("sonnet", "medium"), ("sonnet", "high"), ("opus", "high")], 1
    ):
        record.attempts.append(Attempt(
            number=number, model=model, effort=effort, verdict=Verdict.FAIL,
            summary="the orders repository still imports billing",
            classification=FailureKind.CODE_BUG,
        ))
    record.state = TaskState.FAILED
    state.emit("replanned", tasks=2, replans=1)

    [lesson] = lessons_from(state)

    assert "a (code_bug, tried haiku → sonnet/medium → sonnet/high → opus/high)" \
        in lesson.resolution
    assert "the orders repository still imports billing" in lesson.resolution


def test_a_task_that_recovered_by_escalating_is_not_filed_as_a_reason(tmp_path):
    """One failure then a pass is the ladder working. Filing it here would put a
    solved problem in front of the next plan as though it were open."""
    state = _state(tmp_path)
    record = state.record("a")
    record.attempts.append(Attempt(
        number=1, model="haiku", effort="", verdict=Verdict.FAIL,
        summary="missed the guard clause", classification=FailureKind.CODE_BUG,
    ))
    record.attempts.append(Attempt(
        number=2, model="sonnet", effort="medium", verdict=Verdict.PASS, summary="done",
    ))
    record.state = TaskState.SUCCEEDED
    state.emit("replanned", tasks=2, replans=1)

    [lesson] = [x for x in lessons_from(state) if x.type == "run_replanned"]
    assert lesson.resolution == ""


def test_a_rung_tried_twice_is_named_once(tmp_path):
    """An environment failure retries at the same rung. Listing it twice would
    read as a climb that never happened."""
    state = _state(tmp_path)
    record = state.record("a")
    for number in (1, 2):
        record.attempts.append(Attempt(
            number=number, model="sonnet", effort="medium", verdict=Verdict.FAIL,
            summary="the database was not up", classification=FailureKind.ENVIRONMENT_BUG,
        ))
    state.emit("replanned", tasks=2, replans=1)

    [lesson] = lessons_from(state)
    assert "tried sonnet/medium)" in lesson.resolution


# --- what worked ----------------------------------------------------------
#
# A store of nothing but failures answers "what goes wrong here" and cannot
# answer "what worked". The reusable half of a success is its cost shape.


def _succeeded(state, *task_ids, cost=0.0):
    for task_id in task_ids:
        record = state.record(task_id)
        record.state = TaskState.SUCCEEDED
        record.model, record.effort = "sonnet", "medium"
        record.attempts.append(Attempt(
            number=1, model="sonnet", effort="medium", verdict=Verdict.PASS, summary="ok",
        ))
        record.result = TaskResult(task_id=task_id, verdict=Verdict.PASS, summary="ok")
    if cost:
        state.ledger.spend(task_ids[0], Usage(cost_usd=cost))
    return state


def test_a_clean_run_records_what_it_cost(tmp_path):
    """"Every task passed on its first attempt" is a finding about the plan's
    sizing — this node needs no climb budgeted — and it only shows across runs."""
    state = _succeeded(_state(tmp_path), "a", "b", "c", cost=0.91)

    [lesson] = lessons_from(state)

    assert lesson.type == "run_succeeded"
    assert lesson.severity == "low"
    assert lesson.file_pattern == "run:feature-dev:implement"
    assert "split" not in lesson.description  # the goal, whatever it is
    assert "ship the thing" in lesson.description
    assert "3 task(s), no replans, $0.91" in lesson.resolution
    assert "every task passed on its first attempt" in lesson.resolution


def test_a_success_names_the_task_that_needed_a_bigger_model(tmp_path):
    """The one task that climbed is the lesson. The fifteen that did the expected
    thing would bury it, so they are counted rather than listed."""
    state = _succeeded(_state(tmp_path), "a", "b", "c")
    record = state.record("b")
    record.model, record.effort = "sonnet", "high"
    record.attempts = [
        Attempt(number=1, model="haiku", effort="", verdict=Verdict.FAIL,
                summary="missed the guard", classification=FailureKind.CODE_BUG),
        Attempt(number=2, model="sonnet", effort="high", verdict=Verdict.PASS,
                summary="added the expiry guard on the parser"),
    ]
    record.result = TaskResult(task_id="b", verdict=Verdict.PASS,
                               summary="added the expiry guard on the parser")

    [lesson] = lessons_from(state)

    assert "b (landed at sonnet/high after 2 attempts)" in lesson.resolution
    assert "added the expiry guard on the parser" in lesson.resolution
    assert "\na (" not in lesson.resolution and "\nc (" not in lesson.resolution
    assert "every task passed" not in lesson.resolution


def test_a_run_that_did_not_finish_records_no_success(tmp_path):
    state = _succeeded(_state(tmp_path), "a", "b")
    state.record("c").state = TaskState.FAILED

    assert [x.type for x in lessons_from(state)] == []


def test_a_run_that_replanned_and_then_worked_records_both(tmp_path):
    """The pair worth having: the first shape was wrong for this reason, the
    second worked and cost this much."""
    state = _succeeded(_state(tmp_path), "a", cost=1.40)
    record = state.record("a")
    record.attempts.insert(0, Attempt(
        number=1, model="haiku", effort="", verdict=Verdict.FAIL,
        summary="the spec wants a field the schema forbids",
        classification=FailureKind.SPEC_BUG,
    ))
    state.replans = 1
    state.emit("replanned", tasks=2, replans=1)

    kinds = [x.type for x in lessons_from(state)]

    assert kinds == ["run_replanned", "run_succeeded"]


def test_a_success_reaches_the_global_store(tmp_path):
    """`run:<graph>:<node>` is a statement about a workflow, and a workflow is the
    same one in every repository that installs vise. A first run of that node in
    a new checkout can read what the last one cost."""
    state = _succeeded(_state(tmp_path), "a", cost=0.20)

    assert record_run_lessons(state, str(tmp_path)) == 1

    [entry] = [e for e in get_experience_store().entries if e.type == "run_succeeded"]
    assert "every task passed on its first attempt" in entry.resolution
    assert entry.project_origin == tmp_path.name


def test_each_lesson_goes_to_the_store_its_scope_names(tmp_path):
    """Writing them all to the project store made `scope` a field that described
    nothing: an entry could say "global" and be readable only from the repository
    that produced it."""
    state = _succeeded(_state(tmp_path), "a", cost=0.20)
    state.emit("unroutable", task="c", reason="role frobnicate")

    assert record_run_lessons(state, str(tmp_path)) == 2

    project = {e.type for e in get_project_experience_store(str(tmp_path)).entries}
    shared = {e.type for e in get_experience_store().entries}
    assert project == {"run_blocked"}, "what blocked this checkout stays in it"
    assert "run_succeeded" in shared and "run_blocked" not in shared


def test_the_origin_is_a_project_name_not_a_path(tmp_path):
    """The injector compares this against `Path(project_dir).name`, so a full path
    never matched and marked every runtime lesson foreign to the project that
    produced it — and globally scoped, it would print a local absolute path into
    an unrelated repository's context."""
    state = _succeeded(_state(tmp_path), "a")
    state.emit("unroutable", task="c", reason="role frobnicate")

    origins = {e.project_origin for e in lessons_from(state)}
    assert origins == {tmp_path.name}
    assert not any("/" in o for o in origins)


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
