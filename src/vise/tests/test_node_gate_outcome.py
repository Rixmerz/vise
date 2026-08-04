"""The node gate must count `outcome`, not `source`, when it decides what to
report as "verified" versus "skipped".

`ValidatorRecord.outcome` (verified | unverified | failed) exists so a pass
that ran no real checker — no changed files, pytest exit 5 "no tests
collected", a linter absent from PATH — is distinguishable from a pass that
actually found nothing blocking. Nothing asserted that:

  1. `node_gate.py`'s `checks` payload carries the `outcome` field at all
     (deleting the line that adds it left every other test green).
  2. `node_gate.py`'s `verified_count` / `skipped_count` used to derive from
     `source == "mechanical"`, a different axis (self-grading guard, not
     "did a checker run"). A record can be source="mechanical" (a real tool
     ran) and outcome="unverified" (it had nothing to check) at once —
     exactly `tests_pass`'s pytest-exit-5 path below — and used to be
     counted as verified, silently suppressing the gate_summary hint that
     tells the agent a check did not really run.

The fail-open contract is untouched by this file: these are reporting-only
assertions, pinned alongside a check that the gate still opens.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vise.engines.node_gate import _run_node_validators

# ---------------------------------------------------------------------------
# 1 + 2 — unit level: outcome is carried, and verified/skipped read it
# ---------------------------------------------------------------------------


async def test_mechanical_unverified_pass_is_not_counted_as_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tests_pass's pytest-exit-5 path: source='mechanical', outcome='unverified'."""
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    node = SimpleNamespace(id="n1", validators=[{"type": "tests_pass"}], recipe=None)

    mock_result = MagicMock()
    mock_result.returncode = 5  # pytest: no tests collected
    mock_result.stdout = "no tests ran in 0.01s\n"
    mock_result.stderr = ""

    with patch("shutil.which", return_value="/usr/bin/pytest"), \
            patch("subprocess.run", return_value=mock_result):
        gate = await _run_node_validators(node, str(tmp_path))

    assert gate is not None
    # Fail-open contract: unverified still opens the gate.
    assert gate["passed"] is True, "an unverified pass must still open the gate"

    (check,) = gate["checks"]
    assert check["source"] == "mechanical", "a real subprocess ran — this is not a skip by source"
    assert check["outcome"] == "unverified", "outcome must be carried through to the gate payload"

    assert gate["verified_count"] == 0, (
        "source='mechanical' with outcome='unverified' must not be counted as verified"
    )
    assert gate["skipped_count"] == 1


async def test_outcome_key_present_and_correct_for_a_real_verified_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    node = SimpleNamespace(id="n1", validators=[{"type": "tests_pass"}], recipe=None)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "collected 1 item\n1 passed in 0.05s\n"
    mock_result.stderr = ""

    with patch("shutil.which", return_value="/usr/bin/pytest"), \
            patch("subprocess.run", return_value=mock_result):
        gate = await _run_node_validators(node, str(tmp_path))

    assert gate is not None
    (check,) = gate["checks"]
    assert check["outcome"] == "verified"
    assert gate["verified_count"] == 1
    assert gate["skipped_count"] == 0


# ---------------------------------------------------------------------------
# 3 + 4 — graph level: the hint is not suppressed, and the gate still opens
# ---------------------------------------------------------------------------

GRAPH_YAML = """\
metadata:
  name: "Outcome Gate Test"
nodes:
  - id: "gated"
    name: "Gated"
    is_start: true
    validators:
      - type: tests_pass
  - id: "after"
    name: "After"
    is_end: true
edges:
  - id: "gated-to-after"
    from: "gated"
    to: "after"
"""


@pytest.fixture
def traverse():
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
    from vise.engines.graph_state import load_active_graph

    wf = tmp_path / ".claude" / "workflow"
    wf.mkdir(parents=True)
    (wf / "graph.yaml").write_text(GRAPH_YAML)
    load_active_graph(str(tmp_path))
    return str(tmp_path)


async def test_hint_fires_and_gate_opens_for_an_unverified_pass(
    traverse, project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(Path(project) / "goal"))

    mock_result = MagicMock()
    mock_result.returncode = 5  # pytest: no tests collected → outcome=unverified
    mock_result.stdout = "no tests ran in 0.01s\n"
    mock_result.stderr = ""

    with patch("shutil.which", return_value="/usr/bin/pytest"), \
            patch("subprocess.run", return_value=mock_result):
        result = await traverse(edge_id="gated-to-after", project_dir=project)

    assert not result.get("error"), result
    assert result["new_node"]["id"] == "after", "unverified pass must still open the gate (fail-open)"

    gate_summary = result["gate_summary"]
    assert gate_summary["verified"] == 0
    assert gate_summary["skipped"] == 1
    assert any(c["outcome"] == "unverified" for c in gate_summary["checks"])
    assert "hint" in gate_summary, "an unverified pass must not silently pass as verified"
