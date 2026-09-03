"""The enforcer must block exactly what the workflow declares.

``graph_enforcer.py`` is the PreToolUse gate — it decides whether a tool call
runs — and it reads the graph with its own hand-rolled parser, kept stdlib-only
because it runs before every tool call. That parser used to match ``- id:`` and
``tools_blocked:`` at any depth, which broke three ways, each silent and each
failing OPEN:

* a ``dag`` node whose ``tasks:`` came before its ``tools_blocked:`` lost the
  block list to the last task id;
* ``tools_blocked: ["Bash"]`` — a form the real parser accepts — parsed empty;
* a line of prose in ``prompt_injection: |`` beginning ``- id:`` invented a
  node and took the declaring node's restrictions with it.

The load-bearing test here is the first one: the hook's parser against
``graph_parser.load_graph_from_file`` over the whole bundled library. That
comparison is what found all three, and synthetic cases only ever prove the
shapes someone thought of.

The process-level tests pass ``{**os.environ, ...}`` rather than a scrubbed
env. That is not cosmetic: the pre-existing hook tests replace the environment
wholesale, which strips ``COVERAGE_PROCESS_START`` and is why this hook read
34% while being launched as a process on every run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vise.engines.graph_parser import load_graph_from_file
from vise.engines.graph_state import _get_centralized_state_dir
from vise.hooks.graph_enforcer import parse_tools_blocked

WORKFLOWS = Path(__file__).resolve().parents[1] / "assets" / "workflows"
HOOK = Path(__file__).resolve().parents[1] / "hooks" / "graph_enforcer.py"


# ---------------------------------------------------------------------------
# The two parsers must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "graph_path", sorted(WORKFLOWS.glob("*.yaml")), ids=lambda p: p.stem
)
def test_the_hook_reads_the_same_restrictions_the_workflow_declares(graph_path):
    """One workflow, two parsers, one answer.

    Before the fix this failed on ``research-graph``: the hook reported four
    nodes that do not exist (``primary``, ``secondary``, ``against``,
    ``adjacent`` — the ``gather`` node's task ids). No bundled workflow lost a
    real restriction, but only because none happened to use an affected shape.
    """
    from_hook = parse_tools_blocked(graph_path.read_text(encoding="utf-8"))
    from_real = {
        node.id: list(node.tools_blocked or [])
        for node in load_graph_from_file(graph_path).nodes.values()
    }

    assert set(from_hook) == set(from_real), (
        "the hook and the parser disagree about which nodes exist"
    )
    for node_id in sorted(from_real):
        assert sorted(from_hook[node_id]) == sorted(from_real[node_id]), (
            f"node {node_id!r}: the hook would block {from_hook[node_id]}, "
            f"the workflow declares {from_real[node_id]}"
        )


def test_the_bundled_library_actually_declares_restrictions():
    """Guard against the test above passing because both sides are empty."""
    declared = {
        tool
        for path in WORKFLOWS.glob("*.yaml")
        for tools in parse_tools_blocked(path.read_text(encoding="utf-8")).values()
        for tool in tools
    }
    assert {"Edit", "Write"} <= declared, declared


# ---------------------------------------------------------------------------
# The three shapes that used to fail open
# ---------------------------------------------------------------------------


def _graph(node_body: str) -> str:
    return f'nodes:\n  - id: "a"\n{node_body}edges: []\n'


def test_an_inline_block_list_is_honoured():
    """``tools_blocked: ["Bash"]`` used to parse as empty — a declared gate
    that vise accepted and that blocked nothing."""
    assert parse_tools_blocked(
        _graph('    tools_blocked: ["Bash", "Write"]\n')
    ) == {"a": ["Bash", "Write"]}


def test_an_empty_inline_list_blocks_nothing():
    assert parse_tools_blocked(_graph("    tools_blocked: []\n")) == {"a": []}


def test_a_single_scalar_value_is_honoured():
    assert parse_tools_blocked(_graph('    tools_blocked: "Bash"\n')) == {"a": ["Bash"]}


def test_a_task_list_before_the_restrictions_does_not_swallow_them():
    """The shape every `dag` node is exposed to, and the direction the runtime
    is going. The block list used to land on the last task id instead."""
    parsed = parse_tools_blocked(_graph(
        '    node_type: "dag"\n'
        "    tasks:\n"
        '      - id: "t1"\n'
        '        name: "One"\n'
        '      - id: "t2"\n'
        '        name: "Two"\n'
        "    tools_blocked:\n"
        '      - "Bash"\n'
        '      - "Write"\n'
    ))
    assert parsed == {"a": ["Bash", "Write"]}, (
        "a node's tasks consumed the node's own restrictions"
    )


def test_a_task_list_after_the_restrictions_does_not_extend_them():
    parsed = parse_tools_blocked(_graph(
        "    tools_blocked:\n"
        '      - "Bash"\n'
        "    tasks:\n"
        '      - id: "t1"\n'
    ))
    assert parsed == {"a": ["Bash"]}


def test_a_tasks_own_restrictions_are_not_the_nodes():
    parsed = parse_tools_blocked(_graph(
        "    tasks:\n"
        '      - id: "t1"\n'
        "        tools_blocked:\n"
        '          - "Read"\n'
    ))
    assert parsed == {"a": []}, "a task's restrictions were attributed to its node"


def test_prose_cannot_declare_structure():
    """A ``prompt_injection: |`` is text. It used to be read as YAML."""
    parsed = parse_tools_blocked(_graph(
        "    prompt_injection: |\n"
        '      - id: "falso"\n'
        "      tools_blocked:\n"
        '        - "Read"\n'
        "    tools_blocked:\n"
        '      - "Bash"\n'
    ))
    assert parsed == {"a": ["Bash"]}
    assert "falso" not in parsed


def test_a_folded_scalar_is_also_opaque():
    parsed = parse_tools_blocked(_graph(
        "    description: >\n"
        '      - id: "falso"\n'
        "    tools_blocked:\n"
        '      - "Bash"\n'
    ))
    assert parsed == {"a": ["Bash"]}


# ---------------------------------------------------------------------------
# Shapes that already worked and must keep working
# ---------------------------------------------------------------------------


def test_the_wildcard_survives():
    assert parse_tools_blocked(
        _graph('    tools_blocked:\n      - "*"\n')
    ) == {"a": ["*"]}


def test_a_sequence_at_the_same_indent_as_its_key():
    """Valid YAML, and the form a hand-written workflow is likely to use."""
    assert parse_tools_blocked(
        'nodes:\n- id: "a"\n  tools_blocked:\n  - "Bash"\nedges: []\n'
    ) == {"a": ["Bash"]}


def test_the_edges_section_contributes_nothing():
    parsed = parse_tools_blocked(
        'nodes:\n  - id: "a"\n    tools_blocked:\n      - "Bash"\n'
        'edges:\n  - id: "e"\n    tools_blocked:\n      - "Read"\n'
    )
    assert parsed == {"a": ["Bash"]}


def test_metadata_before_the_nodes_section_contributes_nothing():
    parsed = parse_tools_blocked(
        'metadata:\n  name: "g"\n  tools_blocked:\n    - "Read"\n'
        'nodes:\n  - id: "a"\n    tools_blocked:\n      - "Bash"\nedges: []\n'
    )
    assert parsed == {"a": ["Bash"]}


def test_a_file_with_no_nodes_section_yields_nothing():
    assert parse_tools_blocked("metadata:\n  name: 'g'\n") == {}


def test_whole_line_comments_and_blank_lines_are_ignored():
    """Trailing comments are covered further down, once both parsers learned
    to strip them — see ``test_a_block_list_item_survives_its_comment``."""
    parsed = parse_tools_blocked(
        "nodes:\n"
        "  # the first phase\n"
        '  - id: "a"\n'
        "\n"
        "    tools_blocked:\n"
        "      # no shell in this phase\n"
        '      - "Bash"\n'
        "edges: []\n"
    )
    assert parsed == {"a": ["Bash"]}


# ---------------------------------------------------------------------------
# The hook as a process — the deny path, which had no coverage at all
# ---------------------------------------------------------------------------


BLOCKING_GRAPH = """\
metadata:
  name: "Gate Test"
nodes:
  - id: "think"
    name: "Think"
    is_start: true
    tools_blocked: ["Edit", "Write"]
  - id: "done"
    name: "Done"
    is_end: true
edges:
  - id: "go"
    from: "think"
    to: "done"
"""


@pytest.fixture
def gated(tmp_path: Path, monkeypatch):
    """A project with an active workflow whose current node blocks Edit."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    project = tmp_path / "proj"
    (project / ".claude" / "workflow").mkdir(parents=True)
    (project / ".claude" / "workflow" / "graph.yaml").write_text(
        BLOCKING_GRAPH, encoding="utf-8"
    )

    state_dir = _get_centralized_state_dir(str(project))
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "graph_state.json").write_text(
        json.dumps({"active_graph": "gate-test", "current_nodes": ["think"]}),
        encoding="utf-8",
    )
    return project, home, state_dir


def _run(project: Path, home: Path, tool_name: str) -> tuple[dict, str]:
    """Launch the hook the way Claude Code does, keeping the environment.

    ``{**os.environ, ...}`` rather than a bare dict: the coverage subprocess
    hook lives in ``COVERAGE_PROCESS_START``, and scrubbing it is why this
    file's subject measured 34% while being launched on every run.
    """
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool_name}),
        env={
            **os.environ,
            "CLAUDE_PROJECT_DIR": str(project),
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
        },
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def test_a_blocked_tool_is_denied_on_both_channels(gated):
    project, home, _ = gated

    decision, _ = _run(project, home, "Edit")

    assert decision["decision"] == "block"
    hook_specific = decision["hookSpecificOutput"]
    assert hook_specific["permissionDecision"] == "deny"
    assert hook_specific["hookEventName"] == "PreToolUse"
    # The reason is the teaching surface: it must name what, where, and the
    # way out. A deny that only says "no" gets routed around.
    reason = hook_specific["permissionDecisionReason"]
    assert reason == decision["message"]
    for expected in ("Edit", "think", "gate-test", "graph_traverse", "graph_deactivate"):
        assert expected in reason, expected


def test_an_inline_declared_block_actually_blocks_through_the_hook(gated):
    """End to end for the form that used to parse empty: the graph above
    declares its restrictions inline."""
    project, home, _ = gated
    assert _run(project, home, "Write")[0]["decision"] == "block"


def test_a_tool_the_node_does_not_block_is_approved(gated):
    project, home, _ = gated
    assert _run(project, home, "Read")[0] == {"decision": "approve"}


def test_the_control_tools_pass_even_while_blocking(gated):
    project, home, _ = gated
    for tool in ("mcp__plugin_vise_vise__graph_deactivate",
                 "mcp__vise__graph_status",
                 "graph_reset"):
        assert _run(project, home, tool)[0] == {"decision": "approve"}, tool


def test_the_disabled_flag_releases_the_gate(gated):
    project, home, state_dir = gated
    (state_dir / "config.json").write_text(
        json.dumps({"enforcer_enabled": False}), encoding="utf-8"
    )
    assert _run(project, home, "Edit")[0] == {"decision": "approve"}


def test_a_corrupt_state_file_fails_open_and_says_so(gated):
    project, home, state_dir = gated
    (state_dir / "graph_state.json").write_text("{not json", encoding="utf-8")

    decision, stderr = _run(project, home, "Edit")

    assert decision == {"decision": "approve"}
    assert "vise.enforcer" in stderr, (
        "enforcement died and the session was never told"
    )


def test_a_missing_graph_file_fails_open(gated):
    project, home, _ = gated
    (project / ".claude" / "workflow" / "graph.yaml").unlink()
    assert _run(project, home, "Edit")[0] == {"decision": "approve"}


def test_no_active_workflow_approves(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "proj"
    project.mkdir()
    assert _run(project, home, "Edit")[0] == {"decision": "approve"}


def test_garbage_on_stdin_approves():
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input="not json at all",
        env={**os.environ}, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"decision": "approve"}


# ---------------------------------------------------------------------------
# Trailing comments — the one shape both parsers used to get wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item,expected", [
    ('"Bash"', "Bash"),
    ('"Bash"   # no shell in this phase', "Bash"),
    ("Bash   # no shell in this phase", "Bash"),
    ("'Bash'  # single quotes too", "Bash"),
    ('"Bash#1"', "Bash#1"),
])
def test_a_block_list_item_survives_its_comment(item, expected):
    """This was recorded as "found, not fixed here" when the enforcer's parser
    was rewritten: the real parser mishandled it too — it yielded `'"Bash"'`,
    quotes and all — so teaching only the enforcer would have broken the
    agreement the first test in this file establishes. Both are fixed now, so
    the case belongs here.

    A tool named `'"Bash"'` or `'Bash   # no shell'` matches nothing, so the
    restriction its author wrote blocked nothing, on either side, silently.
    """
    parsed = parse_tools_blocked(_graph(f"    tools_blocked:\n      - {item}\n"))
    assert parsed == {"a": [expected]}


def test_the_two_parsers_agree_on_a_commented_item(tmp_path):
    """The property the fix has to preserve: whatever the enforcer decides, the
    real parser must decide the same, or the agreement is gone."""
    yaml = (
        'nodes:\n  - id: "a"\n    is_start: true\n    tools_blocked:\n'
        '      - "Bash"   # no shell here\n'
        '  - id: "z"\n    is_end: true\n'
        'edges:\n  - id: "e"\n    from: "a"\n    to: "z"\n'
    )
    path = tmp_path / "g.yaml"
    path.write_text(yaml, encoding="utf-8")

    from_hook = parse_tools_blocked(yaml)["a"]
    from_real = load_graph_from_file(path).nodes["a"].tools_blocked

    assert from_hook == list(from_real) == ["Bash"]
