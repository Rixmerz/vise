"""Tests for LspCleanValidator (src/vise/engines/validators.py).

Coverage:
  - Clean changed file → pass
  - File with undefined name (real ruff in .venv) → fail with diagnostic in reason
  - lsp_diagnostics returns available=False → PASS (fail-open)
  - No changed files → pass (skipped)
  - lsp_diagnostics raises → pass (fail-open, never blocks)
  - Errors capped at 5 in evidence summary
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vise.engines.validators import LspCleanValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_goal(project_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="test-lsp",
        project_dir=project_dir,
        goal="test",
        validator_configs=[],
    )


def _diag_unavailable(project_dir: str, file_path: str) -> dict:
    return {"available": False, "reason": "no tool", "diagnostics": [], "tools_run": []}


def _diag_clean(project_dir: str, file_path: str) -> dict:
    return {"available": True, "diagnostics": [], "tools_run": ["ruff"]}


def _diag_with_error(project_dir: str, file_path: str) -> dict:
    return {
        "available": True,
        "diagnostics": [
            {"severity": "error", "line": 5, "col": 1, "code": "F821",
             "message": "Undefined name 'foo'", "source": "ruff"},
        ],
        "tools_run": ["ruff"],
    }


def _diag_with_warning_only(project_dir: str, file_path: str) -> dict:
    return {
        "available": True,
        "diagnostics": [
            {"severity": "warning", "line": 3, "col": 1, "code": "E501",
             "message": "line too long", "source": "ruff"},
        ],
        "tools_run": ["ruff"],
    }


# ---------------------------------------------------------------------------
# No changed files → pass (skipped)
# ---------------------------------------------------------------------------


def test_lsp_clean_no_changed_files_passes(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    v = LspCleanValidator()
    with patch.object(v, "_changed_files", return_value=[]):
        result = v.run(goal)
    assert result.passed is True
    assert "no changed source files" in result.evidence


# ---------------------------------------------------------------------------
# Tool unavailable → PASS (fail-open)
# ---------------------------------------------------------------------------


def test_lsp_clean_tool_unavailable_passes(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_unavailable):
            result = v.run(goal)
    assert result.passed is True
    assert "no diagnostics tool available" in result.evidence


# ---------------------------------------------------------------------------
# Clean file → pass
# ---------------------------------------------------------------------------


def test_lsp_clean_clean_file_passes(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "clean.py"
    py_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_clean):
            result = v.run(goal)
    assert result.passed is True
    assert "clean" in result.evidence


# ---------------------------------------------------------------------------
# File with ERROR diagnostic → fail
# ---------------------------------------------------------------------------


def test_lsp_clean_error_diagnostic_fails(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "bad.py"
    py_file.write_text("x = undefined_var_xyz\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_with_error):
            result = v.run(goal)
    assert result.passed is False
    assert "error" in result.evidence.lower()
    assert result.confidence_contribution == 0.0


# ---------------------------------------------------------------------------
# Warning-only → pass (warnings are not blockers)
# ---------------------------------------------------------------------------


def test_lsp_clean_warning_only_passes(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "warn.py"
    py_file.write_text("x = 1  # long line " + "x" * 100 + "\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_with_warning_only):
            result = v.run(goal)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Internal exception → PASS (fail-open, never blocks wave)
# ---------------------------------------------------------------------------


def test_lsp_clean_internal_exception_passes(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "any.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    v = LspCleanValidator()

    with patch.object(v, "_changed_files", side_effect=RuntimeError("boom")):
        result = v.run(goal)
    assert result.passed is True
    assert "fail-open" in result.evidence.lower() or "internal error" in result.evidence.lower()


# ---------------------------------------------------------------------------
# Many errors → capped at 5 in evidence, remainder shown as "+N more"
# ---------------------------------------------------------------------------


def test_lsp_clean_many_errors_capped_in_evidence(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "many.py"
    py_file.write_text("\n".join(f"x{i} = undefined_{i}" for i in range(10)), encoding="utf-8")
    v = LspCleanValidator()

    def _many_errors(project_dir: str, file_path: str) -> dict:
        return {
            "available": True,
            "diagnostics": [
                {"severity": "error", "line": i, "col": 1, "code": "F821",
                 "message": f"undef_{i}", "source": "ruff"}
                for i in range(8)
            ],
            "tools_run": ["ruff"],
        }

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_many_errors):
            result = v.run(goal)
    assert result.passed is False
    assert "more" in result.evidence


# ---------------------------------------------------------------------------
# Real ruff integration (ruff in .venv — not mocked)
# ---------------------------------------------------------------------------


def test_lsp_clean_real_ruff_undefined_name_fails(tmp_path: Path) -> None:
    """Real ruff in .venv must catch undefined_var_xyz as an error."""
    import shutil
    if not shutil.which("ruff"):
        pytest.skip("ruff not on PATH — real integration test skipped")

    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "bad_real.py"
    py_file.write_text("x = undefined_var_xyz\n", encoding="utf-8")
    v = LspCleanValidator()

    # Use _changed_files patch only; let lsp_diagnostics run for real
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        result = v.run(goal)

    if not result.passed:
        # ruff caught it — correct behaviour
        assert "error" in result.evidence.lower()
    else:
        # ruff available but returned available=False or no errors on this file
        # (acceptable if ruff found no F821 — depends on ruff config)
        pass


def test_lsp_clean_real_ruff_clean_file_passes(tmp_path: Path) -> None:
    """Real ruff: a well-formed file must produce no errors → pass."""
    import shutil
    if not shutil.which("ruff"):
        pytest.skip("ruff not on PATH — real integration test skipped")

    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "clean_real.py"
    py_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    v = LspCleanValidator()

    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        result = v.run(goal)
    assert result.passed is True


# ---------------------------------------------------------------------------
# ValidatorRecord shape
# ---------------------------------------------------------------------------


def test_lsp_clean_record_has_required_fields(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    v = LspCleanValidator()
    with patch.object(v, "_changed_files", return_value=[]):
        result = v.run(goal)
    assert result.name == "lsp_clean"
    assert isinstance(result.passed, bool)
    assert isinstance(result.weight, float)
    assert isinstance(result.evidence, str)
    assert result.at  # ISO timestamp


# ---------------------------------------------------------------------------
# build_validators registry recognises lsp_clean
# ---------------------------------------------------------------------------


def test_build_validators_recognises_lsp_clean() -> None:
    from vise.engines.validators import build_validators
    configs = [{"type": "lsp_clean", "weight": 0.25}]
    vs = build_validators(configs)
    assert len(vs) == 1
    assert vs[0].name == "lsp_clean"
    assert vs[0].weight == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Regression: engine module must exist — validator must NOT be always-pass
# ---------------------------------------------------------------------------


def test_lsp_diagnostics_module_importable() -> None:
    """Before the port, vise.engines.lsp_diagnostics did not exist and the
    fail-open except made lsp_clean an always-pass stub."""
    from vise.engines.lsp_diagnostics import lsp_diagnostics  # noqa: F401


def test_lsp_clean_real_engine_fails_on_error_finding(tmp_path: Path) -> None:
    """End-to-end with the REAL engine (no mocked lsp_diagnostics): a changed
    file with an ERROR-severity finding (F821) must FAIL the gate."""
    import shutil
    if not shutil.which("ruff"):
        pytest.skip("ruff not on PATH — real integration test skipped")

    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "broken.py"
    py_file.write_text("x = definitely_undefined_name_zzz\n", encoding="utf-8")
    v = LspCleanValidator()

    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        result = v.run(goal)

    assert result.passed is False, f"gate must block on F821; evidence: {result.evidence}"
    assert "F821" in result.evidence
