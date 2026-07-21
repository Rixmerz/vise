"""Tests for lsp_diagnostics engine — stateless shell-out diagnostics.

All tests use real ruff (present in .venv/bin) for the happy paths and
monkeypatching for the missing-tool path.  No language server is spawned.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from vise.engines import lsp_diagnostics as diag


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_py(tmp_path: Path):
    """Factory: create a .py file in tmp_path with given content."""

    def _make(name: str, content: str) -> str:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    return _make


# ---------------------------------------------------------------------------
# Real ruff tests (ruff is installed in .venv/bin)
# ---------------------------------------------------------------------------


def test_ruff_catches_undefined_name(tmp_py) -> None:
    """Real ruff must report F821 for an undefined name — core value prop."""
    fp = tmp_py("bad.py", "x = undefined_var_xyz\n")
    result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    # ruff must be available
    assert result.get("available", False), f"ruff not found: {result.get('reason')}"
    assert "ruff" in result["tools_run"]

    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert errors, (
        f"expected ruff to flag undefined_var_xyz as error; got: {result['diagnostics']}"
    )
    codes = {d["code"] for d in errors}
    assert "F821" in codes, f"expected F821 (undefined name); got codes: {codes}"


def test_clean_file_produces_no_errors(tmp_py) -> None:
    """A well-formed .py file should produce no ruff errors."""
    fp = tmp_py(
        "clean.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n",
    )
    result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    assert result.get("available", False), f"ruff not found: {result.get('reason')}"
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert not errors, f"expected no errors on clean file; got: {errors}"


def test_syntax_error_flagged(tmp_py) -> None:
    """ruff must flag a syntax error."""
    fp = tmp_py("syntax.py", "def f(\n")  # unclosed parens → SyntaxError
    result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    assert result.get("available", False), f"ruff not found: {result.get('reason')}"
    # Syntax errors → E9* prefix or similar
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert errors, f"expected syntax error to be flagged; got: {result['diagnostics']}"


def test_result_shape_is_correct(tmp_py) -> None:
    """Every diagnostic entry must have the required shape keys."""
    fp = tmp_py("shape.py", "x = undefined_abc\n")
    result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    assert "available" in result
    assert "diagnostics" in result
    assert "tools_run" in result

    for d in result["diagnostics"]:
        assert "severity" in d, f"missing 'severity' in {d}"
        assert "line" in d, f"missing 'line' in {d}"
        assert "col" in d, f"missing 'col' in {d}"
        assert "message" in d, f"missing 'message' in {d}"
        assert "source" in d, f"missing 'source' in {d}"
        assert d["severity"] in ("error", "warning"), f"unexpected severity: {d['severity']}"


# ---------------------------------------------------------------------------
# Missing-tool path — monkeypatched
# ---------------------------------------------------------------------------


def test_missing_all_checkers_returns_unavailable(tmp_py) -> None:
    """When shutil.which returns None for all checkers, result is available=False."""
    fp = tmp_py("any.py", "x = 1\n")

    with patch.object(diag, "_find_checker", return_value=None):
        result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    assert not result.get("available", True), "expected available=False with no checkers"
    assert "tools_run" in result
    assert result["tools_run"] == []
    assert "reason" in result
    assert "no diagnostics tool" in result["reason"].lower()


def test_ruff_missing_but_mypy_present(tmp_py, monkeypatch: pytest.MonkeyPatch) -> None:
    """When ruff is absent but mypy is present, result is available=True from mypy."""
    fp = tmp_py("any.py", "x = 1\n")
    real_find = diag._find_checker

    def _patched_find(name: str) -> str | None:
        if name == "ruff":
            return None
        return real_find(name)

    monkeypatch.setattr(diag, "_find_checker", _patched_find)
    result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    # Determine whether mypy is actually resolvable via _find_checker (the
    # function the engine uses — NOT bare shutil.which which may differ).
    mypy_path = real_find("mypy")
    if mypy_path is None:
        # mypy also not installed — available=False is correct
        assert not result.get("available", True)
    else:
        assert result.get("available", False), f"expected available=True with mypy; got {result}"
        assert "mypy" in result["tools_run"]
        assert "ruff" not in result["tools_run"]


# ---------------------------------------------------------------------------
# Fail-soft: internal exception
# ---------------------------------------------------------------------------


def test_never_raises_on_exception(tmp_py) -> None:
    """Even if the subprocess runner throws, lsp_diagnostics must not raise."""
    fp = tmp_py("any.py", "x = 1\n")

    with patch.object(diag, "_run_ruff", side_effect=RuntimeError("boom")):
        with patch.object(diag, "_run_mypy", side_effect=RuntimeError("boom")):
            result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    # Should return a structured result, not raise
    assert isinstance(result, dict)
    assert "available" in result


# ---------------------------------------------------------------------------
# Severity classifier unit tests
# ---------------------------------------------------------------------------


def test_severity_f401_is_warning() -> None:
    """F401 (unused import) must map to warning — it is cosmetic lint, not broken code."""
    assert diag._severity_for_ruff("F401") == "warning"


def test_severity_sim105_is_warning() -> None:
    """SIM105 (use contextlib.suppress) must map to warning — style suggestion."""
    assert diag._severity_for_ruff("SIM105") == "warning"


def test_severity_e999_is_error() -> None:
    """E999 (SyntaxError) must map to error — genuinely broken code."""
    assert diag._severity_for_ruff("E999") == "error"


def test_severity_f821_is_error() -> None:
    """F821 (undefined name) must map to error — runtime crash."""
    assert diag._severity_for_ruff("F821") == "error"


def test_severity_f822_is_error() -> None:
    """F822 (undefined name in __all__) must map to error."""
    assert diag._severity_for_ruff("F822") == "error"


def test_severity_f823_is_error() -> None:
    """F823 (local variable referenced before assignment) must map to error."""
    assert diag._severity_for_ruff("F823") == "error"


def test_severity_f831_is_error() -> None:
    """F831 (duplicate argument in function definition) must map to error."""
    assert diag._severity_for_ruff("F831") == "error"


def test_severity_unknown_code_is_warning() -> None:
    """An unrecognised ruff code must default to warning (conservative allowlist)."""
    assert diag._severity_for_ruff("XYZ123") == "warning"


def test_severity_f811_is_warning() -> None:
    """F811 (redefinition of unused name) is cosmetic — must be warning."""
    assert diag._severity_for_ruff("F811") == "warning"


def test_severity_e1xx_is_warning() -> None:
    """E1xx (indentation style) must be warning."""
    assert diag._severity_for_ruff("E101") == "warning"


def test_severity_w_is_warning() -> None:
    """W-series codes must be warning."""
    assert diag._severity_for_ruff("W291") == "warning"


def test_severity_b_is_warning() -> None:
    """B-series (flake8-bugbear) must be warning."""
    assert diag._severity_for_ruff("B006") == "warning"


def test_severity_up_is_warning() -> None:
    """UP-series (pyupgrade) must be warning."""
    assert diag._severity_for_ruff("UP035") == "warning"


def test_severity_e9xx_prefix_is_error() -> None:
    """All E9xx codes (not just E999) must map to error — syntax/token errors."""
    assert diag._severity_for_ruff("E902") == "error"


# ---------------------------------------------------------------------------
# Real-ruff integration: mixed file (F401 warning + F821 error)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not shutil.which("ruff") and diag._find_checker("ruff") is None,
    reason="ruff not available in this environment",
)
def test_real_ruff_f401_warning_and_f821_error(tmp_py) -> None:
    """Real ruff on a file with both F401 and F821: F401 must be warning, F821 must be error.

    This is the key regression test: before the fix, ruff's native severity
    field caused both codes to be classified as 'error', blocking lsp_clean
    on harmless unused-import lint.
    """
    # File has: an unused import (F401) AND an undefined name (F821)
    fp = tmp_py(
        "mixed.py",
        "import os\n"           # F401: os imported but unused
        "x = undefined_xyz\n",  # F821: undefined name
    )
    result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp)

    assert result.get("available", False), f"ruff not found: {result.get('reason')}"
    assert "ruff" in result["tools_run"]

    by_code: dict[str, str] = {d["code"]: d["severity"] for d in result["diagnostics"]}

    # F821 must be error
    assert "F821" in by_code, f"expected F821 in diagnostics; got codes: {list(by_code)}"
    assert by_code["F821"] == "error", f"F821 must be error, got {by_code['F821']!r}"

    # F401 must be warning (not error)
    assert "F401" in by_code, f"expected F401 in diagnostics; got codes: {list(by_code)}"
    assert by_code["F401"] == "warning", f"F401 must be warning, got {by_code['F401']!r}"
