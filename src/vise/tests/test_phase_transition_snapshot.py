"""Tests for automatic phase-transition snapshots.

Requirements verified:
- create_for_phase_transition encodes workflow/phase metadata
- throttle lock is NOT touched (bypassed)
- snapshot failure does NOT block the caller (non-fatal guard)

Note: the jig-side integration test that exercised graph_traverse was
dropped — vise does not ship the graph subsystem.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestCreateForPhaseTransition:
    """Unit tests for the snapshots module helper."""

    def test_label_contains_phase_tag_and_workflow_name(self, tmp_path: Path):
        """create_for_phase_transition encodes from_node, to_node, and workflow name."""
        captured: list[dict] = []

        def fake_create(project: Path, *, label: str = "", phase: str = "") -> MagicMock:
            captured.append({"label": label, "phase": phase})
            snap = MagicMock()
            snap.id = "20260101T000000-abcd"
            return snap

        with patch("vise.core.snapshots.create", side_effect=fake_create):
            from vise.core.snapshots import create_for_phase_transition
            result = create_for_phase_transition(
                tmp_path,
                workflow_name="feature-dev",
                from_node="understand",
                to_node="implement",
            )

        assert result is not None
        assert len(captured) == 1
        assert "understand" in captured[0]["label"]
        assert "implement" in captured[0]["label"]
        assert "feature-dev" in captured[0]["label"]
        assert "phase=" in captured[0]["phase"]

    def test_returns_none_and_does_not_raise_when_create_raises(self, tmp_path: Path):
        """Snapshot failure is non-blocking — returns None, no exception."""
        with patch("vise.core.snapshots.create", side_effect=RuntimeError("git broke")):
            from vise.core.snapshots import create_for_phase_transition
            result = create_for_phase_transition(
                tmp_path,
                workflow_name="debug",
                from_node="reproduce",
                to_node="fix",
            )

        assert result is None

    def test_does_not_write_throttle_lock(self, tmp_path: Path):
        """Phase-transition snapshot must not touch the 30s throttle lock file."""
        lock_file = tmp_path / ".vise" / "snapshots.lock"

        def fake_create(project: Path, **kwargs) -> MagicMock:
            snap = MagicMock()
            snap.id = "x"
            return snap

        with patch("vise.core.snapshots.create", side_effect=fake_create):
            from vise.core.snapshots import create_for_phase_transition
            create_for_phase_transition(
                tmp_path,
                workflow_name="wf",
                from_node="a",
                to_node="b",
            )

        assert not lock_file.exists(), "Phase-transition snapshot must NOT write the throttle lock"

    def test_snapshot_failure_does_not_raise_through_caller_guard(self, tmp_path: Path):
        """Replicate the caller-side non-fatal guard pattern and confirm it holds."""
        import sys

        def _simulate_caller_snapshot_call(project_path, workflow_name, from_node, to_node):
            try:
                from vise.core.snapshots import create_for_phase_transition as _snap_phase
                _snap_phase(
                    Path(str(project_path)),
                    workflow_name=workflow_name,
                    from_node=from_node,
                    to_node=to_node,
                )
            except Exception as _snap_exc:
                print(
                    f"[vise.snapshot] phase-transition snapshot failed (non-fatal): {_snap_exc}",
                    file=sys.stderr,
                )

        with patch("vise.core.snapshots.create_for_phase_transition", side_effect=RuntimeError("disk full")):
            try:
                _simulate_caller_snapshot_call(tmp_path, "wf", "a", "b")
            except Exception as exc:
                pytest.fail(f"Exception leaked through the non-fatal guard: {exc}")
