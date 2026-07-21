"""Tests for vise.engines.validators."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vise.engines.goal_state import Goal, ValidatorRecord
from vise.engines.validators import (
    CapabilityValidator,
    CommandExitValidator,
    FileExistsValidator,
    TestsPassValidator,
    aggregate_confidence,
    build_validators,
    run_validators,
)


def _make_goal(project_dir: str, validator_configs: list[dict] | None = None) -> Goal:
    return Goal(
        id="test-id",
        project_dir=project_dir,
        goal="test goal",
        acceptance_criteria=[],
        target_confidence=0.9,
        complexity="unknown",
        status="active",
        started_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
        validator_configs=validator_configs or [],
    )


def _record(passed: bool, weight: float) -> ValidatorRecord:
    return ValidatorRecord(
        name="test",
        passed=passed,
        confidence_contribution=weight if passed else 0.0,
        weight=weight,
        evidence="",
        at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# aggregate_confidence
# ---------------------------------------------------------------------------

def test_aggregate_confidence_empty_returns_zero() -> None:
    assert aggregate_confidence([]) == 0.0


def test_aggregate_confidence_all_passed() -> None:
    records = [_record(True, 0.6), _record(True, 0.4)]
    assert aggregate_confidence(records) == pytest.approx(1.0)


def test_aggregate_confidence_all_failed() -> None:
    records = [_record(False, 0.6), _record(False, 0.4)]
    assert aggregate_confidence(records) == pytest.approx(0.0)


def test_aggregate_confidence_weighted_correctly() -> None:
    # weight 0.6 passed, weight 0.4 failed → 0.6 / 1.0 = 0.6
    records = [_record(True, 0.6), _record(False, 0.4)]
    assert aggregate_confidence(records) == pytest.approx(0.6)


def test_aggregate_confidence_unequal_weights() -> None:
    # weight 0.3 passed, weight 0.7 passed, weight 0.5 failed → (0.3+0.7)/1.5
    records = [_record(True, 0.3), _record(True, 0.7), _record(False, 0.5)]
    expected = 1.0 / 1.5
    assert aggregate_confidence(records) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# FileExistsValidator
# ---------------------------------------------------------------------------

def test_file_exists_validator_passes_when_all_present(tmp_path: Path) -> None:
    f = tmp_path / "output.txt"
    f.write_text("hello", encoding="utf-8")
    v = FileExistsValidator(paths=("output.txt",), weight=0.5)
    goal = _make_goal(str(tmp_path))
    result = v.run(goal)
    assert result.passed is True
    assert result.confidence_contribution == pytest.approx(0.5)
    assert result.evidence == "all present"
    assert result.source == "mechanical"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# TestsPassValidator — source + evidence persistence
# ---------------------------------------------------------------------------

def test_tests_pass_validator_records_source_exit_code_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    v = TestsPassValidator(weight=0.4)
    goal = _make_goal(str(tmp_path))
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "collected 3 items\n3 passed in 0.10s\n"
    mock_result.stderr = ""
    with patch("shutil.which", return_value="/usr/bin/pytest"), \
            patch("subprocess.run", return_value=mock_result):
        result = v.run(goal)
    assert result.passed is True
    assert result.source == "mechanical"
    assert result.exit_code == 0
    assert result.full_output_path, "full output log path must be set"
    log = Path(result.full_output_path)
    assert log.exists(), "evidence log file must be written to disk"
    assert "3 passed" in log.read_text(encoding="utf-8")


def test_tests_pass_validator_forces_fail_on_failure_marker_despite_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    v = TestsPassValidator(weight=0.4)
    goal = _make_goal(str(tmp_path))
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "collected 5 items\n2 failed, 3 passed in 0.20s\n"
    mock_result.stderr = ""
    with patch("shutil.which", return_value="/usr/bin/pytest"), \
            patch("subprocess.run", return_value=mock_result):
        result = v.run(goal)
    assert result.passed is False, "'2 failed' must force-fail despite exit 0"
    assert "forced-fail" in result.evidence


def test_file_exists_validator_fails_listing_missing(tmp_path: Path) -> None:
    v = FileExistsValidator(paths=("missing_file.txt",), weight=0.5)
    goal = _make_goal(str(tmp_path))
    result = v.run(goal)
    assert result.passed is False
    assert result.confidence_contribution == pytest.approx(0.0)
    assert "missing_file.txt" in result.evidence


def test_file_exists_validator_partial_missing(tmp_path: Path) -> None:
    present = tmp_path / "here.txt"
    present.write_text("x", encoding="utf-8")
    v = FileExistsValidator(paths=("here.txt", "gone.txt"), weight=0.3)
    goal = _make_goal(str(tmp_path))
    result = v.run(goal)
    assert result.passed is False
    assert "gone.txt" in result.evidence


# ---------------------------------------------------------------------------
# CommandExitValidator
# ---------------------------------------------------------------------------

def test_command_exit_validator_passes_on_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    v = CommandExitValidator(cmd=("true",), weight=0.5, name="cmd_test")
    goal = _make_goal(str(tmp_path))
    # mock subprocess so test is portable
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "ok"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        result = v.run(goal)
    assert result.passed is True
    assert result.confidence_contribution == pytest.approx(0.5)
    assert result.source == "mechanical"
    assert result.exit_code == 0
    assert result.full_output_path, "full output log path must be set"
    log = Path(result.full_output_path)
    assert log.exists(), "evidence log file must be written to disk"
    assert "ok" in log.read_text(encoding="utf-8")


def test_command_exit_validator_fails_on_nonzero() -> None:
    v = CommandExitValidator(cmd=("false",), weight=0.4, name="cmd_fail")
    goal = _make_goal("/tmp")
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "error"
    with patch("subprocess.run", return_value=mock_result):
        result = v.run(goal)
    assert result.passed is False
    assert result.confidence_contribution == pytest.approx(0.0)


def test_command_exit_validator_handles_file_not_found() -> None:
    v = CommandExitValidator(cmd=("nonexistent_cmd_xyz",), weight=0.3, name="cmd_missing")
    goal = _make_goal("/tmp")
    with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
        result = v.run(goal)
    assert result.passed is False
    assert "not found" in result.evidence


def test_command_exit_validator_handles_timeout() -> None:
    v = CommandExitValidator(cmd=("sleep", "9999"), weight=0.2, name="cmd_timeout", timeout=1)
    goal = _make_goal("/tmp")
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="sleep", timeout=1)):
        result = v.run(goal)
    assert result.passed is False
    assert result.source == "mechanical"


def test_command_exit_forces_fail_when_output_has_failure_marker_despite_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A green exit must not conceal a failure: exit 0 but a Traceback in the
    output force-fails the record (consistency guard, Edit 6)."""
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    v = CommandExitValidator(cmd=("run-tests",), weight=0.5, name="cmd_guard")
    goal = _make_goal(str(tmp_path))
    mock_result = MagicMock()
    mock_result.returncode = 0  # process claims success
    mock_result.stdout = "running...\nTraceback (most recent call last):\n  oops\n"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        result = v.run(goal)
    assert result.passed is False, "failure marker must force-fail despite exit 0"
    assert result.confidence_contribution == pytest.approx(0.0)
    assert "forced-fail" in result.evidence
    # exit_code still reflects the real (misleading) process exit
    assert result.exit_code == 0


def test_command_exit_does_not_touch_already_failing_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard never flips an already-failing result or appends its note."""
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    v = CommandExitValidator(cmd=("run-tests",), weight=0.5, name="cmd_guard2")
    goal = _make_goal(str(tmp_path))
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "3 failed\n"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        result = v.run(goal)
    assert result.passed is False
    assert "forced-fail" not in result.evidence


# ---------------------------------------------------------------------------
# build_validators
# ---------------------------------------------------------------------------

def test_build_validators_reads_config_list(tmp_path: Path) -> None:
    configs = [
        {"type": "files_exist", "paths": ["a.txt"], "weight": 0.5},
        {"type": "command_exit", "cmd": ["true"], "weight": 0.5},
    ]
    vs = build_validators(configs)
    assert len(vs) == 2
    assert vs[0].name == "files_exist"
    assert vs[1].name == "command_exit"


def test_build_validators_ignores_unknown_type() -> None:
    configs = [
        {"type": "totally_unknown_type", "weight": 0.5},
        {"type": "files_exist", "paths": ["x.txt"], "weight": 0.5},
    ]
    vs = build_validators(configs)
    assert len(vs) == 1
    assert vs[0].name == "files_exist"


def test_build_validators_converts_lists_to_tuples() -> None:
    configs = [{"type": "files_exist", "paths": ["a.txt", "b.txt"], "weight": 0.4}]
    vs = build_validators(configs)
    v = vs[0]
    assert isinstance(v.paths, tuple)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# build_validators — name-as-fallback (Bug 1 regression tests)
# ---------------------------------------------------------------------------

def test_build_validators_accepts_name_as_fallback_for_type() -> None:
    """Model produces {"name": "tests_pass", ...} instead of {"type": ...} — must work."""
    from vise.engines.validators import TestsPassValidator
    configs = [{"name": "tests_pass", "weight": 0.4}]
    vs = build_validators(configs)
    assert len(vs) == 1
    assert isinstance(vs[0], TestsPassValidator)
    assert vs[0].weight == pytest.approx(0.4)


def test_build_validators_type_takes_precedence_over_name() -> None:
    """When both type and name are present, type wins."""
    configs = [{"type": "lint_pass", "name": "tests_pass", "weight": 0.2}]
    vs = build_validators(configs)
    assert len(vs) == 1
    assert vs[0].name == "lint_pass"


def test_build_validators_name_unknown_is_still_skipped() -> None:
    """name that doesn't match any registry key should be skipped, not crash."""
    configs = [{"name": "totally_bogus_validator", "weight": 0.5}]
    vs = build_validators(configs)
    assert vs == []


def test_build_validators_name_fallback_does_not_pass_name_as_kwarg() -> None:
    """The 'name' key must not be forwarded as a constructor kwarg (it is a field default)."""
    # files_exist dataclass has no 'name' constructor arg that we pass externally;
    # but passing it would shadow the dataclass field default — verify it works cleanly.
    configs = [{"name": "files_exist", "paths": ["x.txt"], "weight": 0.3}]
    vs = build_validators(configs)
    assert len(vs) == 1
    assert vs[0].name == "files_exist"


# ---------------------------------------------------------------------------
# run_validators — end-to-end
# ---------------------------------------------------------------------------

def test_run_validators_single_full_weight_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    present = tmp_path / "result.txt"
    present.write_text("done", encoding="utf-8")
    configs = [{"type": "files_exist", "paths": ["result.txt"], "weight": 1.0}]
    goal = _make_goal(str(tmp_path), validator_configs=configs)
    results, confidence = run_validators(goal)
    assert len(results) == 1
    assert results[0].passed is True
    assert confidence == pytest.approx(1.0)


def test_run_validators_empty_configs() -> None:
    goal = _make_goal("/tmp", validator_configs=[])
    results, confidence = run_validators(goal)
    assert results == []
    assert confidence == 0.0


# ---------------------------------------------------------------------------
# CapabilityValidator
# ---------------------------------------------------------------------------

def _patch_capability(monkeypatch, *, resolved, tool_output):
    """Patch the lazy-imported resolve + tool-call seams used by
    CapabilityValidator.run(). Returns nothing; raises in test on misuse.
    """
    pytest.importorskip("vise.recipes.loader", reason="recipes subsystem not yet extracted into vise")
    import vise.recipes.loader as loader_mod
    import vise.recipes.resolver as resolver_mod
    import vise.recipes.runner as runner_mod

    monkeypatch.setattr(loader_mod, "load_capabilities", lambda _p: {})
    monkeypatch.setattr(loader_mod, "load_user_pins", lambda _p: {})
    monkeypatch.setattr(
        resolver_mod, "resolve_capability", lambda _c, _a, _u: resolved
    )

    async def _fake_call(_mcp, _tool, _args):
        return tool_output

    monkeypatch.setattr(runner_mod, "_call_tool", _fake_call)


def test_capability_validator_ok_true_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(monkeypatch, resolved=("layoutlint", "check"), tool_output={"ok": True})
    v = CapabilityValidator(capability="validate.web.layout", args={"target": "x"})
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.exit_code == 0


def test_capability_validator_ok_false_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(monkeypatch, resolved=("layoutlint", "check"), tool_output={"ok": False, "violations": 3})
    v = CapabilityValidator(capability="validate.web.layout")
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is False
    assert rec.exit_code == 1


def test_capability_validator_error_key_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(monkeypatch, resolved=("some", "tool"), tool_output={"error": "boom"})
    v = CapabilityValidator(capability="validate.web.layout")
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is False


def test_capability_validator_okless_dict_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A generic tool that returns a dict with no "ok" and no "error" -> pass.
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(monkeypatch, resolved=("some", "tool"), tool_output={"result": "fine"})
    v = CapabilityValidator(capability="validate.web.layout")
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is True


def test_capability_validator_unresolved_fails_with_clear_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(monkeypatch, resolved=None, tool_output=None)
    v = CapabilityValidator(capability="validate.web.layout")
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is False
    assert "unresolved" in rec.evidence
    assert "capability_set" in rec.evidence


def test_capability_registered_in_registry() -> None:
    built = build_validators([{"type": "capability", "capability": "validate.web.layout", "weight": 1.0}])
    assert len(built) == 1
    assert isinstance(built[0], CapabilityValidator)
    assert built[0].capability == "validate.web.layout"


# ---------------------------------------------------------------------------
# MCP JSON-RPC envelope unwrap (BUG 1 + BUG 2 regression)
#
# The original gap: unit tests mocked _call_tool with a BARE dict in a no-loop
# context, so they never exercised (a) the async node-gate path nor (b) the
# real subprocess-proxy envelope. These tests close both holes.
# ---------------------------------------------------------------------------

def _envelope(structured: dict, *, is_error: bool = False) -> dict:
    """A realistic JSON-RPC envelope as returned by a subprocess proxy."""
    import json as _json
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "structuredContent": structured,
            "content": [{"type": "text", "text": _json.dumps(structured)}],
            "isError": is_error,
        },
    }


def test_capability_run_sync_envelope_ok_false_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No-loop run() path with a REALISTIC envelope (not a bare dict).
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(
        monkeypatch, resolved=("layoutlint", "check"),
        tool_output=_envelope({"ok": False, "summary": "FAIL"}),
    )
    v = CapabilityValidator(capability="validate.web.layout")
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is False, "ok:false inside the envelope must fail"
    assert rec.exit_code == 1


def test_capability_run_sync_envelope_ok_true_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(
        monkeypatch, resolved=("layoutlint", "check"),
        tool_output=_envelope({"ok": True, "summary": "PASS"}),
    )
    v = CapabilityValidator(capability="validate.web.layout")
    rec = v.run(_make_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.exit_code == 0
    # Evidence is the UNWRAPPED summary, not the raw envelope.
    assert "jsonrpc" not in rec.evidence
    assert "PASS" in rec.evidence


@pytest.mark.asyncio
async def test_capability_run_async_envelope_ok_false_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The async node-gate path: run_async must NOT raise (no nested
    # asyncio.run) AND must unwrap ok:false -> fail.
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(
        monkeypatch, resolved=("layoutlint", "check"),
        tool_output=_envelope({"ok": False, "summary": "FAIL"}),
    )
    v = CapabilityValidator(capability="validate.web.layout")
    rec = await v.run_async(_make_goal(str(tmp_path)))
    assert rec.passed is False
    assert rec.exit_code == 1


@pytest.mark.asyncio
async def test_capability_run_async_envelope_ok_true_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(
        monkeypatch, resolved=("layoutlint", "check"),
        tool_output=_envelope({"ok": True, "summary": "PASS"}),
    )
    v = CapabilityValidator(capability="validate.web.layout")
    rec = await v.run_async(_make_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.exit_code == 0


@pytest.mark.asyncio
async def test_capability_run_async_envelope_is_error_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Envelope-level isError must fail even with no ok key in the payload.
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(
        monkeypatch, resolved=("layoutlint", "check"),
        tool_output=_envelope({"summary": "tool crashed"}, is_error=True),
    )
    v = CapabilityValidator(capability="validate.web.layout")
    rec = await v.run_async(_make_goal(str(tmp_path)))
    assert rec.passed is False
    assert rec.exit_code == 1


@pytest.mark.asyncio
async def test_capability_run_async_via_node_gate_no_asyncio_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Full node-gate path inside a running loop: the original bug raised
    # RuntimeError("asyncio.run() cannot be called from a running event loop").
    from types import SimpleNamespace

    from vise.engines.node_gate import _run_node_validators

    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _patch_capability(
        monkeypatch, resolved=("layoutlint", "check"),
        tool_output=_envelope({"ok": False, "summary": "FAIL"}),
    )
    node = SimpleNamespace(
        id="check",
        validators=[{"type": "capability", "capability": "validate.web.layout",
                     "args": {"target": "x"}}],
        recipe=None,
    )
    result = await _run_node_validators(node, str(tmp_path), None)
    assert result is not None
    assert result["passed"] is False, "node-gate must block on ok:false, not error out"
    assert result["failed_count"] == 1
    assert result["failed"][0]["name"] == "capability"
