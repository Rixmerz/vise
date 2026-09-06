"""The debug workflow's tracing node, and the two gates that ship commented.

`strategy-flowtrace` has carried that name since vise's first commit while
bundling no tracer at all — the node's own prompt called it a GAP and pointed
at `capability_set`, a remedy that does not work: the capability taxonomy in
`recipes/capabilities.py` has no tracing entry, so the only thing that would
validate is an invented `x.*` extension with no exemplar behind it.

flowtrace is a real server that answers exactly this node's question. These
tests pin that the node says so without making it a requirement, and that the
two gates reading its output travel with the file rather than living in a
changelog nobody re-reads.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

GRAPH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "assets" / "workflows" / "debug-graph.yaml"
)


@pytest.fixture(scope="module")
def raw() -> str:
    return GRAPH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nodes(raw: str) -> dict:
    return {n["id"]: n for n in yaml.safe_load(raw)["nodes"]}


def _prompt(nodes: dict, node_id: str) -> str:
    return nodes[node_id]["prompt_injection"]


def test_the_tracing_node_no_longer_points_at_a_remedy_that_does_not_work(nodes):
    """It told the reader to bind an instrumentation MCP with `capability_set`
    and drive it via a recipe. The capability taxonomy has no tracing entry, so
    that path validates only as an invented `x.*` extension with no exemplar —
    which is an instruction that reads like a plan and is a dead end."""
    prompt = _prompt(nodes, "strategy-flowtrace")
    assert "capability_set" not in prompt


def test_the_tracing_node_names_the_command_that_produces_a_trace(nodes):
    """Naming the tools without the command that writes the file leaves the
    agent with readers and nothing to read."""
    prompt = _prompt(nodes, "strategy-flowtrace")
    assert "flowtrace run --" in prompt
    assert ".flowtrace/" in prompt


def test_the_tracing_node_teaches_only_contracted_flowtrace_tools(nodes):
    from vise.core.neighbours import FLOWTRACE_TOOLS

    prompt = _prompt(nodes, "strategy-flowtrace")
    named = {tool for tool in FLOWTRACE_TOOLS if tool in prompt}
    assert {"log_open", "trace_find_error", "trace_tree"} <= named, named


def test_the_native_path_survives_a_session_without_flowtrace(nodes):
    """The node routes every performance and integration bug. Making a bundled
    server a requirement would strand every repo that does not have it."""
    prompt = _prompt(nodes, "strategy-flowtrace")
    assert "No flowtrace?" in prompt
    for native in ("cProfile", "pprof", "curl -v"):
        assert native in prompt, f"the fallback lost {native}"


def _flat(text: str) -> str:
    """Prose in a YAML block scalar wraps, and in a comment block every line
    also carries a `#`. A test that matches raw text is really testing where
    the line breaks fell."""
    words = []
    for line in text.splitlines():
        stripped = line.strip()
        words.extend((stripped[1:] if stripped.startswith("#") else stripped).split())
    return " ".join(words)


@pytest.mark.parametrize(
    "warning",
    [
        "Scope to ONE trace id",
        "NOT INSTRUMENTED",
        "an empty trace is almost always that prefix",
        "duration can exceed its parent",
    ],
)
def test_each_way_a_trace_is_misread_is_named(nodes, warning: str):
    """Every one of these produces a confident wrong answer from a correct
    file, which is worse than no trace at all."""
    assert warning in _flat(_prompt(nodes, "strategy-flowtrace"))


def test_the_trace_gates_ship_commented_and_say_why(raw: str):
    """Same reasoning as `diff_scope` in decouple-graph: the artifact belongs
    to a repo that opted in, and a gate failing closed on an artifact nobody
    produces blocks every repo that did not."""
    assert '#   - type: "trace_captured"' in raw
    assert '#   - type: "trace_error_gone"' in raw
    assert "do not re-declare the key" in raw, (
        "a second `validators:` key silently drops the first — the offer has "
        "to say so where it is made"
    )


def test_the_offer_says_what_the_pair_buys_over_tests_pass(raw: str):
    """A commented gate nobody understands is a commented gate nobody enables."""
    assert "a fix that deleted the failing test is green" in _flat(raw)


def test_the_two_halves_are_offered_on_the_nodes_that_can_run_them(nodes, raw: str):
    """`trace_captured` records the signature during the reproduction and
    `trace_error_gone` compares against it after the fix. Offered on the wrong
    nodes, the second reports unverified forever."""
    def offers(block: str) -> set[str]:
        # The commented declaration, not a mention: the reproduce block also
        # explains what the verify half does with what it records, and that
        # sentence is the reason anyone enables either.
        return {
            line.split('"')[1]
            for line in block.splitlines()
            if line.strip().startswith("#   - type:")
        }

    reproduce = raw.split('- id: "reproduce"', 1)[1].split('- id: "unreproducible"', 1)[0]
    verify = raw.split('- id: "verify"', 1)[1].split('- id: "report"', 1)[0]
    assert offers(reproduce) == {"trace_captured"}
    assert offers(verify) == {"trace_error_gone"}
    assert "tests_fail" in reproduce and "tests_pass" in verify
