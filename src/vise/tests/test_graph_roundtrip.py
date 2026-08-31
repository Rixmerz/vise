"""A generated graph, written out and read back, is the same graph.

Tier T4. `graph_builder_save` renders YAML with `_generate_graph_yaml` and then
parses it once — but only to check that it *parses*, not that it parses back to
what was built. Those are different claims, and the gap between them is exactly
where the comment-look-ahead bug lived: `security-audit`'s `fix-criticals`
parsed fine and came back missing its validators, its name and its prompt.

That gap matters more the moment graphs stop being hand-written. A serializer
that loses a field is a persistence layer that loses it — silently, with a
green validation step in front of it — and every graph saved through the
builder goes through this path.

So: build, render, parse, compare field by field. Where PyYAML is available the
same document is read a second way, because a hand-rolled reader is allowed to
support less than PyYAML but never to read the same bytes differently and say
nothing.
"""
from __future__ import annotations

import pytest

from vise.engines.graph_parser import parse_graph_yaml, parse_yaml_simple
from vise.tools._graph_builder import _generate_graph_yaml


def _builder(nodes, edges=(), **meta) -> dict:
    base = {"name": "Round Trip", "description": "d", "version": "1.0.0"}
    base.update(meta)
    return {"metadata": base, "nodes": list(nodes), "edges": list(edges)}


def _node(node_id, **kw):
    node = {"id": node_id, "name": kw.pop("name", node_id.title())}
    node.update(kw)
    return node


def _edge(from_node, to_node, phrases=("advance",)):
    """The *builder's* edge shape, which is flat, not the YAML's nested one."""
    return {
        "id": f"{from_node}-to-{to_node}",
        "from": from_node,
        "to": to_node,
        "condition_type": "phrase",
        "condition_phrases": list(phrases),
    }


def _roundtrip(builder):
    text = _generate_graph_yaml(builder)
    return text, parse_graph_yaml(text)


# --- the fields a node can carry ------------------------------------------


def test_a_minimal_graph_survives_the_round_trip():
    builder = _builder(
        [_node("start", is_start=True), _node("done")],
        [_edge("start", "done")],
    )
    _text, graph = _roundtrip(builder)

    assert set(graph.nodes) == {"start", "done"}
    assert graph.nodes["start"].is_start
    assert graph.nodes["done"].is_end, "a terminal node must come back terminal"


def test_a_prompt_injection_survives_the_round_trip():
    body = (
        "PHASE: IMPLEMENT\n"
        "1. Write the code.\n"
        "2. Run the repo's own checks and quote the output.\n"
    )
    builder = _builder(
        [_node("start", is_start=True, prompt_injection=body), _node("done")],
        [_edge("start", "done")],
    )
    _text, graph = _roundtrip(builder)

    got = graph.nodes["start"].prompt_injection or ""
    assert "PHASE: IMPLEMENT" in got
    assert "quote the output" in got, f"the prompt was truncated: {got!r}"


def test_validators_survive_the_round_trip():
    """The shape that lost three keys to one comment in a shipped workflow."""
    builder = _builder(
        [
            _node("start", is_start=True),
            _node(
                "gate",
                validators=[{"type": "tests_pass", "weight": 0.4},
                            {"type": "lint_pass", "weight": 0.2}],
                prompt_injection="PHASE: GATE\nRun the checks.",
            ),
        ],
        [_edge("start", "gate")],
    )
    _text, graph = _roundtrip(builder)

    node = graph.nodes["gate"]
    assert node.validators == [
        {"type": "tests_pass", "weight": 0.4},
        {"type": "lint_pass", "weight": 0.2},
    ]
    assert node.name == "Gate", "the key after the validators list was dropped"
    assert node.prompt_injection, "and so was the prompt"


def test_tools_blocked_and_mcps_survive_the_round_trip():
    builder = _builder(
        [
            _node("start", is_start=True, tools_blocked=["Edit", "Write"],
                  mcps_enabled=["vise", "github"]),
            _node("done"),
        ],
        [_edge("start", "done")],
    )
    _text, graph = _roundtrip(builder)

    node = graph.nodes["start"]
    assert list(node.tools_blocked) == ["Edit", "Write"]
    assert list(node.mcps_enabled) == ["vise", "github"]


def test_edges_and_their_phrases_survive_the_round_trip():
    builder = _builder(
        [_node("a", is_start=True), _node("b"), _node("c")],
        [_edge("a", "b", ("understood", "advance")), _edge("b", "c", ("done",))],
    )
    _text, graph = _roundtrip(builder)

    by_pair = {(e.from_node, e.to_node): e for e in graph.edges}
    assert set(by_pair) == {("a", "b"), ("b", "c")}
    phrases = by_pair[("a", "b")].condition.phrases
    assert "understood" in phrases and "advance" in phrases


# --- the reader against a reference reader --------------------------------


def test_the_generated_document_reads_the_same_to_pyyaml():
    """vise's reader may support less than PyYAML. It may not disagree quietly."""
    yaml = pytest.importorskip("yaml")

    builder = _builder(
        [
            _node("start", is_start=True, tools_blocked=["Edit"],
                  prompt_injection="PHASE: START\nDo the thing."),
            _node("gate", validators=[{"type": "tests_pass", "weight": 0.4}]),
        ],
        [_edge("start", "gate")],
    )
    text = _generate_graph_yaml(builder)

    ours = parse_yaml_simple(text)
    theirs = yaml.safe_load(text)

    for mine, ref in zip(ours.get("nodes", []), theirs.get("nodes", [])):
        missing = set(ref) - set(mine)
        assert not missing, f"node {ref.get('id')} lost {sorted(missing)}"
        for key, value in ref.items():
            got, want = mine[key], value
            # vise's reader strips the trailing newline a `|` block scalar
            # keeps. That is a normalisation, not a loss, and normalising is
            # allowed — losing content is not. Compared with the ends stripped
            # so this test stays about content.
            if isinstance(got, str) and isinstance(want, str):
                got, want = got.strip(), want.strip()
            assert got == want, (
                f"node {ref.get('id')} key {key!r}: vise reads {mine[key]!r}, "
                f"PyYAML reads {value!r}"
            )


def test_a_saved_graph_is_valid_and_stays_valid():
    """`graph_builder_save` validates before writing. Parsing twice must agree."""
    builder = _builder(
        [_node("start", is_start=True), _node("done")],
        [_edge("start", "done")],
    )
    text = _generate_graph_yaml(builder)

    first = parse_graph_yaml(text)
    second = parse_graph_yaml(_generate_graph_yaml({
        "metadata": builder["metadata"],
        "nodes": [
            {"id": n, "name": node.name, "is_start": node.is_start,
             "is_end": node.is_end}
            for n, node in first.nodes.items()
        ],
        "edges": [
            {"id": e.id, "from": e.from_node, "to": e.to_node,
             "condition_type": e.condition.type,
             "condition_phrases": list(e.condition.phrases or [])}
            for e in first.edges
        ],
    }))

    assert set(second.nodes) == set(first.nodes)
    assert {(e.from_node, e.to_node) for e in second.edges} == {
        (e.from_node, e.to_node) for e in first.edges
    }
