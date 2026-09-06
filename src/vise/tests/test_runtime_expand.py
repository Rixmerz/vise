"""Width from the data: one child task per item of an upstream result.

A `dag` node's tasks are a list written before anyone has seen the data. These
tests pin the other half: a template that becomes as many tasks as its source
found, each an ordinary task, joined by code once every child succeeds. The
ones that matter most are about what must *not* happen — a cap applied in
silence, a missing list reported as an empty one, a resume that invents new
children, a replan that drops the ones it had.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_engine import ForEach, Task
from vise.runtime import expand as ex
from vise.runtime.artifacts import ArtifactStore
from vise.runtime.contracts import (
    Artifact,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.registry import AgentRegistry, AgentSpec, capability_hint
from vise.runtime.replan import REMEDIATION_SUFFIX
from vise.runtime.scheduler import Scheduler, SchedulerConfig
from vise.runtime.state import RunState
from vise.runtime.worker import MockWorker

RUN = "r-wide"


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    for spec in (
        AgentSpec(id="researcher", role="research", description="d", model="sonnet",
                  writes=False, capabilities=("research",)),
        AgentSpec(id="backend-python", role="backend", description="d", model="sonnet",
                  capabilities=("backend", "python")),
        # The replanner staffs its re-specification task from this role.
        AgentSpec(id="designer", role="design", description="d", model="opus",
                  writes=False, capabilities=("design",)),
    ):
        reg.agents[spec.id] = spec
    return reg


def _pass(task_id: str, **kw) -> TaskResult:
    base = dict(task_id=task_id, verdict=Verdict.PASS, summary="ok",
                evidence="$ look\nfound", checks="$ ruff\nok", usage=Usage(cost_usd=0.1))
    base.update(kw)
    return TaskResult(**base)


def _fail(task_id: str, classification=FailureKind.CODE_BUG) -> TaskResult:
    return TaskResult(task_id=task_id, verdict=Verdict.FAIL, summary="wrong",
                      classification=classification, usage=Usage(cost_usd=0.1))


def _split_result(items, key="sub_questions", kind="plan") -> TaskResult:
    return _pass("split", artifacts=(
        Artifact(run_id=RUN, task_id="split", kind=kind, payload={key: items}),
    ))


def _tasks(*, cap: int = 0, key: str = "sub_questions", prompt: str = "Answer: {item}",
           with_synth: bool = True) -> list[Task]:
    tasks = [
        Task(id="split", name="Split", role="research", writes=False),
        Task(id="each", name="Each", role="research", writes=False, prompt=prompt,
             dependencies=["split"],
             for_each=ForEach(from_task="split", items=key, max_items=cap)),
    ]
    if with_synth:
        tasks.append(Task(id="synth", name="Synth", role="research", writes=False,
                          dependencies=["each"]))
    return tasks


def _spec(**kw) -> RunSpec:
    base = dict(run_id=RUN, goal="g", project_dir="/nonexistent-not-a-repo",
                budget=RunBudget(max_parallel=4))
    base.update(kw)
    return RunSpec(**base)


def _scheduler(tmp_path: Path, worker=None, **kw) -> Scheduler:
    kw.setdefault("registry", _registry())
    kw.setdefault("artifacts", ArtifactStore(tmp_path / "artifacts", RUN))
    kw.setdefault("state_root", tmp_path / "state")
    return Scheduler(worker=worker or MockWorker(), **kw)


def _run(tmp_path: Path, tasks, worker=None, **kw) -> tuple[RunState, MockWorker]:
    worker = worker or MockWorker()
    return _scheduler(tmp_path, worker, **kw).run(_spec(), tasks), worker


def _briefed(worker: MockWorker) -> list[str]:
    return [b.task_id for b in worker.briefs]


# --- the pure half ---------------------------------------------------------


def test_the_default_cap_is_not_wide_research_wide():
    """A child is a `claude -p` session on this machine, not a VM on a fleet."""
    assert 1 <= ex.DEFAULT_MAX_ITEMS <= 50


def test_render_substitutes_item_and_item_key_and_nothing_else():
    assert ex.render("Q: {item}", "why") == "Q: why"
    assert ex.render("{item.name} / {item}", {"name": "x"}) == 'x / {"name": "x"}'
    assert ex.render("{item.missing} {other} {}", {"name": "x"}) == "{item.missing} {other} {}"


def test_children_are_derived_the_same_way_twice():
    """What lets a resumed run find its children's records instead of inventing new ones."""
    template = _tasks()[1]
    first = ex.expand(template, ["a", "b", "c"], cap=5)
    second = ex.expand(template, ["a", "b", "c"], cap=5)
    assert [c.id for c in first.children] == [c.id for c in second.children] == [
        "each[1]", "each[2]", "each[3]"
    ]


def test_a_child_shares_no_list_with_its_template_or_siblings():
    """The replanner rewrites `dependencies` in place; a shared list would give
    every child every sibling's re-specification task."""
    template = _tasks()[1]
    children = ex.expand(template, ["a", "b"], cap=5).children
    children[0].dependencies.append("ghost")
    assert "ghost" not in children[1].dependencies
    assert "ghost" not in template.dependencies


def test_a_child_carries_its_item_even_when_the_prompt_never_asked():
    template = Task(id="t", name="T", role="research", writes=False, prompt="Do the thing.",
                    for_each=ForEach(from_task="s", items="k"), dependencies=["s"])
    child = ex.expand(template, ["only"], cap=5).children[0]
    assert "item 1/1: only" in (child.prompt or "")
    assert child.for_each is None, "a child must never expand again"


def test_ownership_and_acceptance_are_rendered_per_item():
    template = Task(id="t", name="T", role="backend", ownership=["src/{item}/**"],
                    acceptance=["{item} is wired"], dependencies=["s"],
                    for_each=ForEach(from_task="s", items="k"))
    a, b = ex.expand(template, ["alpha", "beta"], cap=5).children
    assert (a.ownership, b.ownership) == (["src/alpha/**"], ["src/beta/**"])
    assert a.acceptance == ["alpha is wired"]


def test_listing_tells_missing_from_empty():
    arts = [Artifact(run_id=RUN, task_id="s", kind="plan", payload={"other": 1, "k": []})]
    assert ex.listing(arts, "k").items == ()
    missing = ex.listing(arts, "nope")
    assert missing.items is None and missing.carried == ("k", "other")
    not_a_list = ex.listing([Artifact(run_id=RUN, task_id="s", kind="p", payload={"k": "x"})], "k")
    assert not_a_list.items is None


def test_a_child_routes_as_its_template_does():
    """`capability_hint` splits the id on non-word characters; the index must
    not read as a capability and the template's language must still reach."""
    assert capability_hint(Task(id="backend-python[3]", name="x")) == "python"
    assert capability_hint(Task(id="gather[3]", name="x")) is None


# --- the loop ---------------------------------------------------------------


def test_one_child_per_item_and_no_worker_for_the_template(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result(["a", "b", "c"])]})
    state, worker = _run(tmp_path, _tasks(), worker)

    briefed = _briefed(worker)
    assert briefed.count("each[1]") == briefed.count("each[2]") == briefed.count("each[3]") == 1
    assert "each" not in briefed, "the template is the join, never a worker"
    prompts = {b.task_id: b.prompt for b in worker.briefs}
    assert "Answer: b" in prompts["each[2]"]
    assert state.succeeded(), {k: (v.state.value, v.note) for k, v in state.tasks.items()}


def test_the_join_costs_nothing_and_writes_the_collection(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result(["a", "b"])]})
    sched = _scheduler(tmp_path, worker)
    state = sched.run(_spec(), _tasks())

    assert state.tasks["each"].state is TaskState.SUCCEEDED
    assert "each" not in state.ledger.by_task
    art = sched.artifacts.get("each", ex.COLLECTION_KIND)
    assert art is not None
    assert art.payload["count"] == 2 and art.payload["dropped"] == 0
    assert [c["state"] for c in art.payload["children"]] == ["succeeded", "succeeded"]
    assert [c["item"] for c in art.payload["children"]] == ["a", "b"]
    assert any(e["kind"] == "joined" and e["children"] == 2 for e in state.events)


def test_a_child_is_a_task_like_any_other(tmp_path):
    """A wrong answer escalates one rung, carrying the attempt, as for any task."""
    worker = MockWorker(scripted={
        "split": [_split_result(["a", "b"])],
        "each[2]": [_fail("each[2]"), _pass("each[2]")],
    })
    state, worker = _run(tmp_path, _tasks(), worker)

    tries = [b for b in worker.briefs if b.task_id == "each[2]"]
    assert len(tries) == 2
    assert tries[1].attempts and tries[1].attempts[0].verdict is Verdict.FAIL
    assert (tries[0].model, tries[0].effort) != (tries[1].model, tries[1].effort)
    assert state.succeeded()


def test_one_failed_child_keeps_the_join_and_its_dependants_from_succeeding(tmp_path):
    """The synthesis must not start on eleven of twelve answers."""
    worker = MockWorker(scripted={
        "split": [_split_result(["a", "b", "c"])],
        "each[2]": [_fail("each[2]")] * 6,
    })
    state, worker = _run(tmp_path, _tasks(), worker,
                         config=SchedulerConfig(max_replans=0))

    assert state.tasks["each[2]"].state is TaskState.FAILED
    assert state.tasks["each"].state is not TaskState.SUCCEEDED
    assert "synth" not in _briefed(worker)
    assert "each[2]" in state.human_gate, state.human_gate
    assert state.tasks["each[1]"].state is TaskState.SUCCEEDED, "siblings keep their pass"


def test_an_empty_list_joins_with_zero_children_and_says_so(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result([])]})
    state, worker = _run(tmp_path, _tasks(), worker)

    assert state.tasks["each"].state is TaskState.SUCCEEDED
    assert "0 item(s)" in state.tasks["each"].note
    assert not [b for b in worker.briefs if b.task_id.startswith("each[")]
    assert "synth" in _briefed(worker), "downstream still runs; nothing is a finding"
    assert state.succeeded()


def test_a_missing_list_blocks_the_template_and_names_what_was_there(tmp_path):
    """`found: 0` and "could not look" are different facts and must not render alike."""
    worker = MockWorker(scripted={"split": [_split_result(["a"], key="questions")]})
    state, worker = _run(tmp_path, _tasks(), worker)

    record = state.tasks["each"]
    assert record.state is TaskState.BLOCKED
    assert "split" in record.note and "sub_questions" in record.note
    assert "questions" in record.note, "the keys the source did carry are named"
    assert "synth" not in _briefed(worker)
    assert not state.succeeded()
    assert any(e["kind"] == "expansion_blocked" for e in state.events)


def test_a_source_with_no_artifacts_at_all_is_told_apart_from_a_wrong_key(tmp_path):
    worker = MockWorker(scripted={"split": [_pass("split")]})
    state, _ = _run(tmp_path, _tasks(), worker)
    assert "no artifacts at all" in state.tasks["each"].note


def test_without_an_artifact_store_the_template_blocks_rather_than_guessing(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result(["a"])]})
    state, _ = _run(tmp_path, _tasks(), worker, artifacts=None)
    assert state.tasks["each"].state is TaskState.BLOCKED
    assert "artifact store" in state.tasks["each"].note


def test_the_cap_cuts_the_list_and_the_cut_is_on_the_record(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result(["a", "b", "c", "d", "e"])]})
    sched = _scheduler(tmp_path, worker)
    state = sched.run(_spec(), _tasks(cap=2))

    children = [t for t in state.tasks if t.startswith("each[")]
    assert sorted(children) == ["each[1]", "each[2]"]
    cut = [e for e in state.events if e["kind"] == "expansion_truncated"]
    assert cut and cut[0]["dropped"] == 3 and cut[0]["cap"] == 2 and cut[0]["offered"] == 5
    assert "3 item(s) dropped" in state.tasks["each"].note
    assert sched.artifacts.get("each", ex.COLLECTION_KIND).payload["dropped"] == 3


def test_an_undeclared_cap_is_the_default_never_unlimited(tmp_path):
    many = [f"q{i}" for i in range(ex.DEFAULT_MAX_ITEMS + 5)]
    worker = MockWorker(scripted={"split": [_split_result(many)]})
    state, _ = _run(tmp_path, _tasks(), worker)
    assert len([t for t in state.tasks if t.startswith("each[")]) == ex.DEFAULT_MAX_ITEMS


def test_downstream_sees_the_children_and_the_collection(tmp_path):
    worker = MockWorker(scripted={
        "split": [_split_result(["a", "b"])],
        "each[1]": [_pass("each[1]", artifacts=(
            Artifact(run_id=RUN, task_id="each[1]", kind="finding", payload={"answer": "A"}),
        ))],
    })
    state, worker = _run(tmp_path, _tasks(), worker)

    synth = next(b for b in worker.briefs if b.task_id == "synth")
    kinds = {(a.task_id, a.kind) for a in synth.inputs}
    assert ("each[1]", "finding") in kinds
    assert ("each", ex.COLLECTION_KIND) in kinds
    assert state.succeeded()


def test_the_width_is_bounded_by_the_lanes(tmp_path):
    """Twenty items and two lanes: never more than two children in flight."""
    import threading

    lock = threading.Lock()
    live = {"now": 0, "peak": 0}

    class _Counting(MockWorker):
        def run(self, brief):
            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            try:
                return super().run(brief)
            finally:
                with lock:
                    live["now"] -= 1

    worker = _Counting(scripted={"split": [_split_result([f"q{i}" for i in range(20)])]})
    sched = _scheduler(tmp_path, worker)
    state = sched.run(_spec(budget=RunBudget(max_parallel=2)), _tasks(with_synth=False))
    assert state.succeeded()
    assert live["peak"] <= 2


def test_a_replan_keeps_the_children(tmp_path):
    """The replanner is handed the live list. Handed the graph's, it would
    cancel every child as "dropped by a replan"."""
    worker = MockWorker(scripted={
        "split": [_split_result(["a", "b", "c"])],
        "each[2]": [_fail("each[2]", FailureKind.SPEC_BUG), _pass("each[2]")],
    })
    state, worker = _run(tmp_path, _tasks(), worker)

    assert state.replans == 1
    assert f"each[2]{REMEDIATION_SUFFIX}" in state.tasks
    assert all(state.tasks[c].state is TaskState.SUCCEEDED
               for c in ("each[1]", "each[2]", "each[3]")), {
        k: v.state.value for k, v in state.tasks.items()
    }
    assert not any(v.note == "dropped by a replan" for v in state.tasks.values())
    assert state.succeeded()


def test_a_resumed_run_finds_the_same_children(tmp_path):
    """The stop leaves a template mid-expansion. Resuming must derive the same
    ids, keep the children that passed, and retry only the one that did not."""
    worker = MockWorker(scripted={
        "split": [_split_result(["a", "b", "c"])],
        "each[2]": [_fail("each[2]")] * 6,
    })
    sched = _scheduler(tmp_path, worker, config=SchedulerConfig(replanner=None))
    parked = sched.run(_spec(), _tasks())
    assert parked.human_gate and parked.tasks["each[1]"].state is TaskState.SUCCEEDED
    assert parked.expansions["each"]["children"] == ["each[1]", "each[2]", "each[3]"]

    loaded = RunState.load(tmp_path / "state", RUN)
    assert loaded is not None and loaded.expansions == parked.expansions

    healed = MockWorker()
    again = _scheduler(tmp_path, healed, config=SchedulerConfig(replanner=None))
    state = again.resume(loaded, _tasks())

    assert state.succeeded(), {k: (v.state.value, v.note) for k, v in state.tasks.items()}
    retried = _briefed(healed)
    assert "each[2]" in retried
    assert "each[1]" not in retried and "each[3]" not in retried, "paid for once"
    assert "split" not in retried
    assert sorted(t for t in state.tasks if t.startswith("each[")) == [
        "each[1]", "each[2]", "each[3]"
    ]


def test_the_expansion_survives_save_and_load(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result(["a", "b"])]})
    state, _ = _run(tmp_path, _tasks(), worker)
    loaded = RunState.load(tmp_path / "state", RUN)
    assert loaded.expansions["each"] == {
        "source": "split", "key": "sub_questions", "items": ["a", "b"],
        "children": ["each[1]", "each[2]"], "dropped": 0,
    }


def test_the_events_narrate_the_expansion(tmp_path):
    worker = MockWorker(scripted={"split": [_split_result(["a", "b"])]})
    state, _ = _run(tmp_path, _tasks(), worker)
    kinds = [e["kind"] for e in state.events]
    assert kinds.index("expanded") < kinds.index("joined")
    expanded = next(e for e in state.events if e["kind"] == "expanded")
    assert (expanded["source"], expanded["key"], expanded["count"]) == ("split", "sub_questions", 2)


def test_a_template_that_requires_a_human_passes_the_flag_to_its_children(tmp_path):
    tasks = _tasks(with_synth=False)
    tasks[1].requires_human = True
    worker = MockWorker(scripted={"split": [_split_result(["a"])]})
    state, worker = _run(tmp_path, tasks, worker)
    assert "each[1]" in state.human_gate
    assert "each[1]" not in _briefed(worker)


@pytest.mark.parametrize("items", [["a", {"name": "b"}], [{"name": "x", "path": "src/x"}]])
def test_object_items_reach_the_child_as_json(tmp_path, items):
    worker = MockWorker(scripted={"split": [_split_result(items)]})
    state, worker = _run(tmp_path, _tasks(prompt="Do {item.name} / {item}"), worker)
    last = next(b for b in worker.briefs if b.task_id == f"each[{len(items)}]")
    assert '"name"' in last.prompt
    assert state.succeeded()
