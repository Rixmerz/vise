"""The runtime's task metadata lives on the graph's own Task, not beside it.

That decision is the one worth pinning: a second task type with its own ids and
its own dependency edges would be a second workflow engine, and the two would
disagree within a release. These tests hold the seam — the parser reads the new
fields, the builder writes them back, an invalid enum fails closed, and a
workflow that declares none of them is byte-identical to what it was before.
"""
from __future__ import annotations

import pytest

from vise.engines.graph_parser import GraphParseError, parse_graph_yaml
from vise.tools._graph_builder import _generate_graph_yaml

RUNTIME_YAML = """
metadata:
  name: "runtime-fields"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "backend-auth"
        name: "JWT middleware"
        role: "backend"
        criticality: "elevated"
        complexity: "high"
        model: "opus"
        effort: "high"
        max_cost: 1.5
        max_turns: 12
        timeout_s: 120
        ownership:
          - "src/auth/**"
        acceptance:
          - "login returns 200"
      - id: "review"
        name: "Review"
        role: "review"
        writes: false
        dependencies:
          - "backend-auth"
edges: []
"""

PLAIN_YAML = """
metadata:
  name: "plain"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "t1"
        name: "One"
edges: []
"""


def _tasks(yaml_text: str):
    graph = parse_graph_yaml(yaml_text)
    return {t.id: t for t in graph.nodes["implement"].tasks}


def test_every_runtime_field_survives_the_parser():
    task = _tasks(RUNTIME_YAML)["backend-auth"]
    assert task.role == "backend"
    assert task.criticality == "elevated"
    assert task.complexity == "high"
    assert task.model == "opus"
    assert task.effort == "high"
    assert task.ownership == ["src/auth/**"]
    assert task.acceptance == ["login returns 200"]
    assert (task.max_cost, task.max_turns, task.timeout_s) == (1.5, 12, 120)
    assert task.writes is True


def test_writes_false_is_read_as_false_not_as_a_truthy_string():
    assert _tasks(RUNTIME_YAML)["review"].writes is False


def test_a_task_declaring_nothing_keeps_todays_defaults():
    task = _tasks(PLAIN_YAML)["t1"]
    assert task.role is None
    assert task.ownership == []
    assert task.criticality == "routine"
    assert task.complexity == "medium"
    assert task.writes is True
    assert (task.model, task.effort) == (None, None)


@pytest.mark.parametrize(
    "field,value",
    [("criticality", "urgent"), ("complexity", "extreme"), ("effort", "turbo")],
)
def test_an_invalid_runtime_enum_fails_closed(field, value):
    """A typo'd criticality falling back to 'routine' would route a
    security-critical task to the cheapest model and look like it worked."""
    bad = PLAIN_YAML.replace('        name: "One"', f'        name: "One"\n        {field}: "{value}"')
    with pytest.raises(GraphParseError) as exc:
        parse_graph_yaml(bad)
    assert field in str(exc.value)


def test_a_single_string_ownership_is_accepted_as_a_one_item_list():
    yaml_text = PLAIN_YAML.replace(
        '        name: "One"', '        name: "One"\n        ownership: "src/**"'
    )
    assert _tasks(yaml_text)["t1"].ownership == ["src/**"]


def test_the_builder_emits_the_runtime_fields_it_was_given():
    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag", "is_start": True, "is_end": True,
            "tasks": [{
                "id": "t1", "name": "One", "role": "backend", "criticality": "critical",
                "ownership": ["src/**"], "acceptance": ["it works"], "writes": False,
                "model": "opus", "max_cost": 2.0,
            }],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    for fragment in ('role: "backend"', 'criticality: "critical"', "writes: false",
                     '- "src/**"', '- "it works"', 'model: "opus"', "max_cost: 2.0"):
        assert fragment in rendered, fragment
    assert _tasks(rendered)["t1"].role == "backend"


def test_the_builder_writes_nothing_for_a_task_that_declared_nothing():
    """A graph authored without runtime metadata must round-trip unchanged."""
    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag", "is_start": True, "is_end": True,
            "tasks": [{"id": "t1", "name": "One"}],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    for absent in ("role:", "criticality:", "complexity:", "ownership:", "writes:",
                   "acceptance:", "max_cost:", "max_turns:", "timeout_s:"):
        assert absent not in rendered, absent


def test_the_bundled_workflows_still_parse():
    """The nine shipped graphs predate every field above and must be untouched."""
    from pathlib import Path

    import vise
    workflows = Path(vise.__file__).parent / "assets" / "workflows"
    graphs = sorted(workflows.glob("*-graph.yaml"))
    assert graphs, "no bundled workflows found"
    for path in graphs:
        graph = parse_graph_yaml(path.read_text(encoding="utf-8"))
        assert graph.nodes, path.name


def test_requires_human_survives_the_parser_and_the_builder():
    yaml_text = PLAIN_YAML.replace(
        '        name: "One"', '        name: "One"\n        requires_human: true'
    )
    assert _tasks(yaml_text)["t1"].requires_human is True

    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag",
            "is_start": True, "is_end": True,
            "tasks": [{"id": "t1", "name": "One", "requires_human": True}],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    assert "requires_human: true" in rendered
    assert _tasks(rendered)["t1"].requires_human is True


def test_a_task_that_declares_nothing_does_not_require_a_human():
    assert _tasks(PLAIN_YAML)["t1"].requires_human is False


def test_a_declared_default_still_means_what_was_declared():
    """The emitter omits defaults; that is only safe while the parser agrees.

    `_generate_graph_yaml` writes `writes: false` and stays silent on
    `writes: true`, because true is what a task without the key already means.
    The two halves of that bargain live in different files — the omission is in
    the builder, the default is in the parser — and nothing made them agree.
    A parser default flipping to `False` would turn every composed writing task
    into a read-only one, silently, and the spec gate that fires on `writes`
    would stop firing.

    So this asserts the property rather than the text: declare each default
    explicitly, emit, parse, and require the value to come back.
    """
    declared = {
        "writes": True,
        "criticality": "routine",
        "complexity": "medium",
        "requires_human": False,
    }
    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "N", "node_type": "dag",
            "is_start": True, "is_end": True,
            "tasks": [{"id": "t1", "name": "One", **declared}],
        }],
        "edges": [],
    }

    rendered = _generate_graph_yaml(builder)
    task = _tasks(rendered)["t1"]

    for field, value in declared.items():
        assert field not in rendered, f"{field} is a default and should be omitted"
        assert getattr(task, field) == value, (
            f"{field} was declared {value!r} and came back {getattr(task, field)!r}"
        )


# --- for_each: width from the data ------------------------------------------


FOR_EACH_YAML = """
metadata:
  name: "wide"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "split"
        name: "Split"
        role: "research"
        writes: false
      - id: "each"
        name: "Each"
        role: "research"
        writes: false
        for_each:
          from: "split"
          items: "sub_questions"
          max_items: 8
        prompt: |
          Answer this one: {item}
edges: []
"""


def test_for_each_survives_the_parser_and_adds_the_source_as_a_dependency():
    """The source is what the expansion waits on. Asking the author to repeat
    it under `dependencies` would be a second place for one fact to be wrong."""
    task = _tasks(FOR_EACH_YAML)["each"]
    assert task.for_each is not None
    assert (task.for_each.from_task, task.for_each.items, task.for_each.max_items) == (
        "split", "sub_questions", 8
    )
    assert task.dependencies == ["split"]
    assert "{item}" in (task.prompt or ""), "the placeholder reaches the runtime untouched"


def test_a_source_already_declared_as_a_dependency_is_not_duplicated():
    yaml_text = FOR_EACH_YAML.replace(
        '        for_each:', '        dependencies: ["split"]\n        for_each:'
    )
    assert _tasks(yaml_text)["each"].dependencies == ["split"]


def test_a_task_declaring_nothing_does_not_expand():
    assert _tasks(PLAIN_YAML)["t1"].for_each is None


def test_the_cap_defaults_to_zero_meaning_the_runtimes_default_never_unlimited():
    yaml_text = FOR_EACH_YAML.replace("          max_items: 8\n", "")
    assert _tasks(yaml_text)["each"].for_each.max_items == 0


@pytest.mark.parametrize(
    "block,fragment",
    [
        ('for_each:\n          items: "x"', "needs 'from'"),
        ('for_each:\n          from: "split"', "needs 'items'"),
        ('for_each:\n          from: "split"\n          items: "x"\n          max_items: -1',
         "max_items"),
        ('for_each:\n          from: "split"\n          items: "x"\n          max_items: true',
         "max_items"),
        ('for_each: "split"', "not a mapping"),
    ],
)
def test_a_half_said_for_each_fails_closed(block, fragment):
    """There is no list to guess at, and a cap that silently became unlimited
    would look like it worked — once, on the bill."""
    yaml_text = FOR_EACH_YAML.replace(
        'for_each:\n          from: "split"\n          items: "sub_questions"\n          max_items: 8',
        block,
    )
    with pytest.raises(GraphParseError) as exc:
        parse_graph_yaml(yaml_text)
    assert fragment in str(exc.value)


def test_a_task_that_expands_from_itself_is_refused():
    with pytest.raises(GraphParseError) as exc:
        parse_graph_yaml(FOR_EACH_YAML.replace('from: "split"', 'from: "each"'))
    assert "expands from itself" in str(exc.value)


def test_a_task_that_expands_from_an_unknown_task_is_refused():
    with pytest.raises(GraphParseError) as exc:
        parse_graph_yaml(FOR_EACH_YAML.replace('from: "split"', 'from: "ghost"'))
    assert "ghost" in str(exc.value)


def test_a_hand_built_expansion_that_does_not_wait_on_its_source_is_refused():
    """The parser adds the dependency; a Task built in code has to carry it, or
    the children would be derived from a list that does not exist yet."""
    from vise.engines.graph_engine import ForEach, Graph, Node, Task

    node = Node(
        id="n", name="n", node_type="dag", is_start=True, is_end=True,
        tasks=[
            Task(id="split", name="split"),
            Task(id="each", name="each", for_each=ForEach(from_task="split", items="k")),
        ],
    )
    graph = Graph(nodes={"n": node}, edges=[])
    errors = graph.validate()
    assert any("does not depend on it" in e for e in errors), errors
    node.tasks[1].dependencies = ["split"]
    assert not [e for e in graph.validate() if "each" in e]


def test_the_builder_round_trips_for_each():
    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag",
            "is_start": True, "is_end": True,
            "tasks": [
                {"id": "split", "name": "Split", "role": "research", "writes": False},
                {"id": "each", "name": "Each", "role": "research", "writes": False,
                 "for_each": {"from": "split", "items": "sub_questions", "max_items": 5}},
            ],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    assert 'from: "split"' in rendered and 'items: "sub_questions"' in rendered
    assert "max_items: 5" in rendered
    task = _tasks(rendered)["each"]
    assert (task.for_each.from_task, task.for_each.items, task.for_each.max_items) == (
        "split", "sub_questions", 5
    )
    assert task.dependencies == ["split"]

    builder["nodes"][0]["tasks"][1]["for_each"].pop("max_items")
    rendered = _generate_graph_yaml(builder)
    assert "max_items" not in rendered
    assert _tasks(rendered)["each"].for_each.max_items == 0
