"""The node gate, driven through the real ``graph_traverse`` tool.

vise's central promise is "a phase cannot be left until its declared checks
pass". Nothing exercised the tool that enforces it: ``_graph_transition.py`` sat
at 7% line coverage, and the only test naming ``graph_traverse`` asserted the
string appears in an emitted CLI prompt. So every bundled gate — release's
``tests_pass``, security-audit's ``verify``, feature-dev's ``validate`` — was
enforced by code no test ever ran.

These lock the four properties the gate exists to have:

  1. A failing validator BLOCKS the traversal and leaves the current node put.
     (Returning an error while advancing anyway would pass a test that only
     inspects the return value — so node position is asserted separately.)
  2. A passing validator lets it through.
  3. ``VISE_NODE_GATE_OVERRIDE=1`` — the documented escape hatch — opens a
     failing gate, and nothing else does.
  4. Failed attempts are counted and persisted, so plateau detection has real
     input rather than a counter that resets every call.

Plus the ``validators_green`` edge's fail-closed contract: an edge of that type
whose source node declares no validators must refuse, never wave through.

The gate is driven with ``files_exist`` on purpose: no subprocess, nothing on
PATH, no runner to detect — the check's outcome is exactly "did the test create
this file", which makes a red here mean the gate broke and nothing else.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_state import load_active_graph, load_graph_state, save_graph_state

GRAPH_YAML = """\
metadata:
  name: "Gate Test"
nodes:
  - id: "gated"
    name: "Gated"
    is_start: true
    validators:
      - type: files_exist
        paths: ["done.txt"]
        weight: 1.0
  - id: "after"
    name: "After"
  - id: "bare"
    name: "Bare"
  - id: "end"
    name: "End"
    is_end: true
edges:
  - id: "gated-to-after"
    from: "gated"
    to: "after"
  - id: "gated-green"
    from: "gated"
    to: "end"
    condition:
      type: "validators_green"
  # `bare` exists to own a validators_green edge with nothing to prove it green.
  # It needs an inbound edge only because the parser rejects orphan nodes.
  - id: "after-to-bare"
    from: "after"
    to: "bare"
  - id: "bare-green"
    from: "bare"
    to: "end"
    condition:
      type: "validators_green"
"""


@pytest.fixture
def traverse():
    """The real registered ``graph_traverse`` callable.

    Same capture harness as test_graph_deactivate's graph_status test: the tools
    are defined inside ``register_*(mcp)`` closures, so a fake MCP is the only
    way to reach them without standing up a server.
    """
    registered: dict = {}

    class _FakeMCP:
        def tool(self, *a, **kw):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    from vise.tools._graph_transition import register_graph_transition_tools

    register_graph_transition_tools(_FakeMCP())
    return registered["graph_traverse"]


@pytest.fixture
def project(tmp_path: Path) -> str:
    """A project with the gate-test graph active and sitting on ``gated``."""
    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(GRAPH_YAML)
    load_active_graph(str(tmp_path))  # lazy-init → current node is the start node
    return str(tmp_path)


def _at(project_dir: str) -> str | None:
    return load_graph_state(project_dir).get_current_node()


def _move_to(project_dir: str, node_id: str) -> None:
    state = load_graph_state(project_dir)
    state.current_nodes = [node_id]
    save_graph_state(project_dir, state)


# ---------------------------------------------------------------------------
# 1 + 2 — the gate blocks, then opens
# ---------------------------------------------------------------------------

async def test_failing_validator_blocks_the_traversal(traverse, project):
    assert _at(project) == "gated"

    result = await traverse(edge_id="gated-to-after", project_dir=project)

    assert result.get("node_gate_blocked") is True, result
    assert result.get("error") is True
    assert _at(project) == "gated", "gate reported blocked but the phase advanced anyway"


async def test_passing_validator_opens_the_gate(traverse, project):
    (Path(project) / "done.txt").write_text("")

    result = await traverse(edge_id="gated-to-after", project_dir=project)

    assert not result.get("error"), result
    assert _at(project) == "after"


async def test_gate_evidence_names_the_failing_validator(traverse, project):
    """A blocked gate that doesn't say what failed sends the agent guessing."""
    result = await traverse(edge_id="gated-to-after", project_dir=project)

    failed = result["gate_details"]["failed"]
    assert [f["name"] for f in failed] == ["files_exist"]
    assert "done.txt" in failed[0]["evidence"]


# ---------------------------------------------------------------------------
# 3 — the documented escape hatch, and only it
# ---------------------------------------------------------------------------

async def test_override_env_opens_a_failing_gate(traverse, project, monkeypatch):
    monkeypatch.setenv("VISE_NODE_GATE_OVERRIDE", "1")

    result = await traverse(edge_id="gated-to-after", project_dir=project)

    assert not result.get("error"), result
    assert _at(project) == "after"


async def test_override_env_must_be_exactly_one(traverse, project, monkeypatch):
    """A truthy-looking value is not the escape hatch — only "1" is."""
    monkeypatch.setenv("VISE_NODE_GATE_OVERRIDE", "true")

    result = await traverse(edge_id="gated-to-after", project_dir=project)

    assert result.get("node_gate_blocked") is True, result
    assert _at(project) == "gated"


# ---------------------------------------------------------------------------
# 4 — attempts are counted across calls, on disk
# ---------------------------------------------------------------------------

async def test_failed_attempts_accumulate_in_persisted_state(traverse, project):
    for expected in (1, 2, 3):
        result = await traverse(edge_id="gated-to-after", project_dir=project)
        assert result["attempts"] == expected
        on_disk = load_graph_state(project).node_gate_state["gated"]["attempts"]
        assert on_disk == expected, "attempt count did not survive the save"


# ---------------------------------------------------------------------------
# validators_green edges
# ---------------------------------------------------------------------------

async def test_validators_green_edge_refuses_when_source_declares_no_validators(
    traverse, project
):
    """Fail-closed: no declared checks means the edge cannot be proven green."""
    _move_to(project, "bare")

    result = await traverse(edge_id="bare-green", project_dir=project)

    assert result.get("validators_green_blocked") is True, result
    assert "declares no validators" in result["message"]
    assert _at(project) == "bare"


async def test_validators_green_edge_blocks_while_the_source_gate_is_red(traverse, project):
    result = await traverse(edge_id="gated-green", project_dir=project)

    assert result.get("error") is True, result
    assert _at(project) == "gated"


async def test_validators_green_edge_opens_once_the_source_gate_is_green(traverse, project):
    (Path(project) / "done.txt").write_text("")

    result = await traverse(edge_id="gated-green", project_dir=project)

    assert not result.get("error"), result
    assert _at(project) == "end"


async def test_a_validators_green_traversal_runs_the_validators_only_once(
    traverse, project, monkeypatch
):
    """The node gate and the validators_green check must share one run.

    Both call ``_run_node_validators`` on the SAME node with the same state, so
    a second call bought nothing and doubled the cost. On feature-dev's `test`
    node — whose only outgoing edge is validators_green — that meant running the
    entire test suite twice on every attempt to leave the phase, which is also
    why the quality-gate workflow avoided validators_green edges entirely.
    """
    from vise.tools import _graph_transition as gt

    real = gt._run_node_validators
    calls = 0

    async def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(gt, "_run_node_validators", counting)
    (Path(project) / "done.txt").write_text("")

    result = await traverse(edge_id="gated-green", project_dir=project)

    assert not result.get("error"), result
    assert calls == 1, f"validators ran {calls} times for one traversal"


# ---------------------------------------------------------------------------
# edge-selection guards — cheap, and they protect the gate from being skipped
# ---------------------------------------------------------------------------

async def test_unknown_edge_is_rejected_and_lists_the_real_ones(traverse, project):
    result = await traverse(edge_id="does-not-exist", project_dir=project)

    assert result.get("error") is True
    assert set(result["available_edges"]) == {"gated-to-after", "gated-green"}
    assert _at(project) == "gated"


async def test_edge_from_another_node_cannot_be_used_to_skip_the_gate(traverse, project):
    """`bare-green` starts at `bare`; standing on `gated` it must not fire."""
    result = await traverse(edge_id="bare-green", project_dir=project)

    assert result.get("error") is True
    assert "does not start from current node" in result["message"]
    assert _at(project) == "gated"


# ---------------------------------------------------------------------------
# graph_activate — the way in. Covered here because a dead "refresh project
# metadata + pattern catalog" step was removed from the middle of it, and
# nothing else in the suite runs the function.
# ---------------------------------------------------------------------------

async def test_activate_lands_on_the_start_node_without_stderr_noise(tmp_path, capsys):
    registered: dict = {}

    class _FakeMCP:
        def tool(self, *a, **kw):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    from vise.tools._graph_management import register_graph_management_tools

    register_graph_management_tools(_FakeMCP())

    library = tmp_path / ".claude" / "workflows"
    library.mkdir(parents=True)
    (library / "gate-test.yaml").write_text(GRAPH_YAML)

    result = await registered["graph_activate"](
        graph_name="gate-test", project_dir=str(tmp_path)
    )

    assert result.get("success") is True, result
    assert result["current_node"]["id"] == "gated"
    assert result["node_count"] == 4
    assert _at(str(tmp_path)) == "gated"
    # Activation used to print two `refresh failed (non-fatal)` warnings naming
    # engines that never shipped. Nothing is broken, so nothing should complain.
    assert "non-fatal" not in capsys.readouterr().err
