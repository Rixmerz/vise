"""A gate must report what it proved, not merely that nothing failed.

``_run_node_validators`` returned ``passed``, ``failed_count``, ``failed`` and
``confidence`` — every PASSING record was discarded. A validator that skipped
(its tool is unconfigured or not on PATH) reports ``passed=True`` with
``source="asserted"``, so a node declaring 13 quality checks against a repo
that binds none of them returned a dict byte-identical to a node that had
mechanically verified all 13:

    {"passed": true, "failed_count": 0, "failed": [], "confidence": 1.0}

That made ``quality-gate-graph``'s own instruction — read the evidence of every
check, including the ones that passed — impossible to obey, because no channel
carried a skip to the agent. The whole skip-pass design rests on the agent
being able to tell a skip from a verification.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vise.engines.graph_parser import load_graph_from_file
from vise.engines.graph_state import GraphState
from vise.engines.node_gate import _run_node_validators

WORKFLOWS = Path(__file__).resolve().parents[1] / "assets" / "workflows"


@pytest.fixture
def unconfigured_repo() -> str:
    """A project with no .vise/quality.yaml — every quality_check will skip."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


async def test_all_skipped_is_distinguishable_from_all_verified(
    unconfigured_repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISE_QUALITY_PROFILE", raising=False)
    graph = load_graph_from_file(WORKFLOWS / "quality-gate-graph.yaml")

    result = await _run_node_validators(
        graph.nodes["static"], unconfigured_repo, GraphState()
    )

    assert result is not None
    assert result["passed"] is True, "an unbound check must not block the node"
    assert result["failed_count"] == 0
    # ...and yet nothing was actually checked. That must be visible.
    assert result["verified_count"] == 0, "nothing ran, so nothing was verified"
    assert result["skipped_count"] == 5, "all five static checks are unbound here"


async def test_every_check_reports_its_name_source_and_evidence(
    unconfigured_repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counts alone do not tell the agent WHICH gap to close."""
    monkeypatch.delenv("VISE_QUALITY_PROFILE", raising=False)
    graph = load_graph_from_file(WORKFLOWS / "quality-gate-graph.yaml")

    result = await _run_node_validators(
        graph.nodes["security"], unconfigured_repo, GraphState()
    )

    assert result is not None
    checks = result["checks"]
    assert len(checks) == 4, "one entry per declared validator, passing ones included"
    for check in checks:
        assert check["source"] == "asserted", (
            "a skipped check must never claim source='mechanical' — goal_complete "
            "grades on that field and would accept it as a verified pass"
        )
        assert check["evidence"], "an evidence-less skip is a silent one"

    named = " ".join(c["evidence"] for c in checks)
    for check_name in ("sast", "sca", "secrets", "contracts"):
        assert check_name in named, f"evidence must name the unbound check {check_name!r}"


async def test_a_real_pass_is_marked_mechanical(
    unconfigured_repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counters must actually distinguish — not just always report skipped."""
    profile = Path(unconfigured_repo) / "quality.yaml"
    # `true` exits 0 on any POSIX box; no project toolchain required.
    profile.write_text('checks:\n  lint: ["true"]\n', encoding="utf-8")
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    graph = load_graph_from_file(WORKFLOWS / "quality-gate-graph.yaml")
    result = await _run_node_validators(
        graph.nodes["static"], unconfigured_repo, GraphState()
    )

    assert result is not None
    assert result["verified_count"] == 1, "the bound `lint` check really ran"
    assert result["skipped_count"] == 4, "the other four are still unbound"
    lint = next(c for c in result["checks"] if "lint" in c["evidence"] or c["passed"])
    assert lint["source"] == "mechanical"
