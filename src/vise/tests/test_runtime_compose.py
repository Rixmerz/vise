"""What a finished run says about the plan that should follow it.

The two planes were complete and disconnected: `graph_builder_*` accepts a
whole plan, `RunState` records everything a next plan would want to know, and
between them was a person reading `state.json`.

This brief is that gap, and it stops one step short of closing the loop on
purpose. It dispatches nothing — a person still starts a run, by the same
documented decision that keeps `run_start` out of the MCP surface.

The assertions below are mostly about one property: a composer must never have
to re-derive which work is already paid for. Getting that wrong is how a
follow-up plan repeats a task that succeeded, which is the specific waste this
runtime's whole ledger exists to prevent.
"""
from __future__ import annotations

import argparse
import json

import pytest

from vise.runtime.compose import ComposeBrief, brief_from
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
from vise.runtime.state import RunState
from vise.tools._graph_builder import BUILDER_VALIDATORS


def _state(tmp_path, **spec_fields) -> RunState:
    spec = RunSpec(
        run_id="r1", goal="ship the thing", project_dir=str(tmp_path),
        graph_name="feature-dev", node_id="implement",
        budget=RunBudget(max_parallel=2), **spec_fields,
    )
    return RunState.for_tasks(spec, ["a", "b", "c"])


def _succeed(state, task_id):
    record = state.record(task_id)
    record.state = TaskState.SUCCEEDED
    record.result = TaskResult(task_id=task_id, verdict=Verdict.PASS, summary="done")


# --- what it reads ---------------------------------------------------------


def test_succeeded_work_is_named_so_it_is_not_planned_again(tmp_path):
    state = _state(tmp_path)
    _succeed(state, "a")
    _succeed(state, "b")

    brief = brief_from(state)

    assert brief.succeeded == ("a", "b")
    assert [o.task_id for o in brief.unfinished] == ["c"]


def test_a_failure_carries_what_it_said_and_how_it_was_classified(tmp_path):
    state = _state(tmp_path)
    record = state.record("c")
    record.state = TaskState.FAILED
    record.result = TaskResult(
        task_id="c", verdict=Verdict.FAIL,
        summary="the schema forbids the field the spec asks for",
        classification=FailureKind.SPEC_BUG,
    )

    [outcome] = [o for o in brief_from(state).unfinished if o.task_id == "c"]

    assert outcome.state == "failed"
    assert outcome.classification == "spec_bug"
    assert "the schema forbids" in outcome.reason


def test_the_classification_survives_a_result_that_lost_it(tmp_path):
    """After a retry the record's result may be a later attempt; the attempt
    that carried the classification is the one that said why."""
    state = _state(tmp_path)
    record = state.record("c")
    record.state = TaskState.BLOCKED
    record.attempts.append(Attempt(
        number=1, model="m", effort="e", verdict=Verdict.FAIL,
        summary="wrong question", classification=FailureKind.ARCHITECTURE_BUG,
    ))

    [outcome] = [o for o in brief_from(state).unfinished if o.task_id == "c"]

    assert outcome.classification == "architecture_bug"
    assert outcome.reason == "wrong question"


def test_a_task_with_only_a_note_still_says_something(tmp_path):
    state = _state(tmp_path)
    record = state.record("c")
    record.state = TaskState.BLOCKED
    record.note = "ownership held by b"

    [outcome] = [o for o in brief_from(state).unfinished if o.task_id == "c"]
    assert outcome.reason == "ownership held by b"


# --- what it says about the plan itself ------------------------------------


def test_a_parked_run_says_so(tmp_path):
    state = _state(tmp_path)
    state.stop_for_human("a person must decide the API shape")

    brief = brief_from(state)

    assert any("a person must decide" in line for line in brief.plan_level)


def test_a_cancelled_run_says_so(tmp_path):
    state = _state(tmp_path)
    state.cancelled = True
    state.cancel_reason = "sentinel"

    assert any("sentinel" in line for line in brief_from(state).plan_level)


def test_an_unroutable_task_is_a_plan_problem_not_a_work_problem(tmp_path):
    state = _state(tmp_path)
    state.emit("unroutable", task="c", reason="role frobnicate")

    assert any("frobnicate" in line for line in brief_from(state).plan_level)


def test_a_replan_classification_is_called_out_as_the_plan_being_wrong(tmp_path):
    state = _state(tmp_path)
    record = state.record("c")
    record.state = TaskState.FAILED
    record.result = TaskResult(
        task_id="c", verdict=Verdict.FAIL, summary="s",
        classification=FailureKind.SPEC_BUG,
    )

    lines = brief_from(state).plan_level

    assert any("the plan was wrong, not the work" in line for line in lines)


def test_the_same_plan_problem_twice_is_said_once(tmp_path):
    state = _state(tmp_path)
    state.emit("unroutable", task="c", reason="role frobnicate")
    state.emit("unroutable", task="c", reason="role frobnicate")

    lines = [x for x in brief_from(state).plan_level if "frobnicate" in x]
    assert len(lines) == 1


def test_the_lessons_the_run_recorded_come_along(tmp_path):
    state = _state(tmp_path)
    state.emit("human_gate", task="c", reason="a person must decide")

    assert any("a person must decide" in line for line in brief_from(state).lessons)


def test_the_money_already_spent_is_stated(tmp_path):
    state = _state(tmp_path)
    state.ledger.spend("a", Usage(cost_usd=3.25, tokens_in=1, tokens_out=1))
    state.replans = 1

    brief = brief_from(state)

    assert brief.spent_usd == pytest.approx(3.25)
    assert brief.replans == 1


# --- the constraint --------------------------------------------------------


def test_the_brief_states_which_validators_a_composed_node_may_declare(tmp_path):
    brief = brief_from(_state(tmp_path))

    assert set(brief.allowed_validators) == set(BUILDER_VALIDATORS)
    assert "command_exit" not in brief.allowed_validators
    assert "quality_check" not in brief.allowed_validators


def test_the_rendered_brief_says_why_the_two_are_refused(tmp_path):
    rendered = brief_from(_state(tmp_path)).render()

    assert "command_exit" in rendered and "quality_check" in rendered
    assert "judged by" in rendered


def test_the_rendered_brief_says_nothing_dispatches(tmp_path):
    """The decision this module does not reopen."""
    # Normalised: the sentence wraps in the rendered output, and this test is
    # about what it says, not where it breaks.
    rendered = " ".join(brief_from(_state(tmp_path)).render().split())

    assert "Nothing here dispatches" in rendered
    assert "a person reads the plan and runs it" in rendered


# --- nothing to compose ----------------------------------------------------


def test_a_finished_run_needs_no_new_plan(tmp_path):
    state = _state(tmp_path)
    for task_id in ("a", "b", "c"):
        _succeed(state, task_id)

    brief = brief_from(state)

    assert brief.needs_a_new_plan is False
    assert "nothing to compose" in brief.render()


def test_an_unfinished_run_needs_one(tmp_path):
    state = _state(tmp_path)
    _succeed(state, "a")
    assert brief_from(state).needs_a_new_plan is True


def test_the_dict_round_trips_the_shape_a_machine_reads(tmp_path):
    state = _state(tmp_path)
    _succeed(state, "a")
    state.record("c").state = TaskState.BLOCKED

    payload = json.loads(json.dumps(brief_from(state).to_dict()))

    assert payload["succeeded"] == ["a"]
    assert {o["task"] for o in payload["unfinished"]} == {"b", "c"}
    assert payload["needs_a_new_plan"] is True
    assert "tests_pass" in payload["allowed_validators"]


# --- the command -----------------------------------------------------------


def _compose(root, run_id="r1", as_json=False) -> int:
    from vise.cli.runtime_cmd import _cmd_compose

    return _cmd_compose(argparse.Namespace(
        run_id=run_id, state_dir=str(root), json=as_json,
    ))


def test_the_command_renders_the_brief(tmp_path, capsys):
    root = tmp_path / "state"
    state = _state(tmp_path)
    _succeed(state, "a")
    state.save(root)

    rc = _compose(root)

    out = capsys.readouterr().out
    assert rc == 0, "a run with work left is the case that has a plan to write"
    assert "ship the thing" in out
    assert "already done" in out and "a" in out


def test_the_command_exits_three_when_there_is_nothing_to_compose(tmp_path, capsys):
    """Distinct from 0 so a script can tell "wrote a brief" from "run is done"."""
    root = tmp_path / "state"
    state = _state(tmp_path)
    for task_id in ("a", "b", "c"):
        _succeed(state, task_id)
    state.save(root)

    assert _compose(root) == 3
    assert "nothing to compose" in capsys.readouterr().out


def test_the_command_speaks_json(tmp_path, capsys):
    root = tmp_path / "state"
    _state(tmp_path).save(root)

    _compose(root, as_json=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "r1"
    assert payload["allowed_validators"]


def test_the_command_refuses_a_run_that_does_not_exist(tmp_path):
    with pytest.raises(SystemExit) as exc:
        _compose(tmp_path, run_id="ghost")
    assert exc.value.code == 2


def test_the_brief_is_constructible_without_a_run(tmp_path):
    """It is a dataclass, and its default allowlist must come from one place."""
    brief = ComposeBrief(run_id="x", goal="g", project_dir=str(tmp_path))
    assert set(brief.allowed_validators) == set(BUILDER_VALIDATORS)


def test_a_plan_level_event_without_a_detail_does_not_render_a_dangling_label(tmp_path):
    """`replan_unavailable:` claims something happened and refuses to say what.

    Found by running `vise runtime compose` against a real recorded run rather
    than by reading the code. The runs on disk predate the emitter carrying a
    reason, so the brief has to render them honestly rather than decorate them
    with a separator that leads nowhere.
    """
    state = _state(tmp_path)
    state.emit("replan_unavailable")
    brief = brief_from(state)
    assert "replan_unavailable" in brief.plan_level
    assert not any(line.endswith(":") for line in brief.plan_level)


def test_a_plan_level_event_with_a_detail_still_names_it(tmp_path):
    state = _state(tmp_path)
    state.emit("replan_unavailable", task="parser", reason="no replanner is configured")
    brief = brief_from(state)
    assert "replan_unavailable: parser no replanner is configured" in brief.plan_level
