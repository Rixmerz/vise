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

    def _patched_find(name: str, project_dir=None) -> str | None:
        if name == "ruff":
            return None
        return real_find(name, project_dir)

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


# ---------------------------------------------------------------------------
# go vet — per-file checker.  Subprocess boundary always mocked: go is not
# installed in this environment, and a skipped test is a test that never
# runs.
# ---------------------------------------------------------------------------


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    from types import SimpleNamespace
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_go_vet_available_and_clean(tmp_py) -> None:
    fp = tmp_py("clean.go", "package main\n")
    with patch.object(diag, "_find_checker", return_value="/usr/bin/go"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stderr="")):
            result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp, tools=("go_vet",))

    assert result["available"] is True
    assert result["tools_run"] == ["go_vet"]
    assert result["diagnostics"] == []


def test_go_vet_available_and_broken(tmp_py) -> None:
    fp = tmp_py("bad.go", "package main\n")
    vet_stderr = f"{fp}:3:2: Printf call has arguments but no formatting directives\n"
    with patch.object(diag, "_find_checker", return_value="/usr/bin/go"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stderr=vet_stderr, returncode=1)):
            result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp, tools=("go_vet",))

    assert result["available"] is True
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert errors, f"expected go vet finding to be classified as error; got {result['diagnostics']}"
    assert errors[0]["source"] == "go vet"
    assert errors[0]["line"] == 3


def test_go_vet_absent_is_skipped(tmp_py) -> None:
    fp = tmp_py("any.go", "package main\n")
    with patch.object(diag, "_find_checker", return_value=None):
        result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp, tools=("go_vet",))

    assert result["available"] is False
    assert result["tools_run"] == []


# ---------------------------------------------------------------------------
# cargo check --message-format=json — whole-project checker.
# ---------------------------------------------------------------------------


def _cargo_line(level: str, message: str, file_name: str, line: int = 1, col: int = 1, code: str | None = None) -> str:
    import json as _json
    obj = {
        "reason": "compiler-message",
        "message": {
            "level": level,
            "message": message,
            "code": {"code": code} if code else None,
            "spans": [
                {"is_primary": True, "file_name": file_name, "line_start": line, "column_start": col},
            ],
        },
    }
    return _json.dumps(obj)


def test_cargo_check_available_and_clean(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value="/usr/bin/cargo"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stdout="")):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("cargo",))

    assert result["available"] is True
    assert result["diagnostics"] == []


def test_cargo_check_available_and_broken(tmp_path: Path) -> None:
    stdout = _cargo_line("error", "mismatched types", "src/main.rs", line=10, col=5, code="E0308") + "\n"
    with patch.object(diag, "_find_checker", return_value="/usr/bin/cargo"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stdout=stdout)):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("cargo",))

    assert result["available"] is True
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert errors, f"expected cargo error to be classified as error; got {result['diagnostics']}"
    assert errors[0]["file"] == str(Path(tmp_path) / "src/main.rs")


def test_cargo_check_warning_level_is_warning(tmp_path: Path) -> None:
    stdout = _cargo_line("warning", "unused variable", "src/lib.rs") + "\n"
    with patch.object(diag, "_find_checker", return_value="/usr/bin/cargo"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stdout=stdout)):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("cargo",))

    assert result["diagnostics"][0]["severity"] == "warning"


def test_cargo_check_absent_is_skipped(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value=None):
        result = diag.lsp_diagnostics_project(str(tmp_path), tools=("cargo",))

    assert result["available"] is False


# ---------------------------------------------------------------------------
# tsc --noEmit — whole-project checker.
# ---------------------------------------------------------------------------


def test_tsc_available_and_clean(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value="/usr/bin/tsc"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stdout="")):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("tsc",))

    assert result["available"] is True
    assert result["diagnostics"] == []


def test_tsc_available_and_broken(tmp_path: Path) -> None:
    stdout = "src/index.ts(12,7): error TS2345: Argument of type 'string' is not assignable.\n"
    with patch.object(diag, "_find_checker", return_value="/usr/bin/tsc"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stdout=stdout)):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("tsc",))

    assert result["available"] is True
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert errors, f"expected tsc error to be classified as error; got {result['diagnostics']}"
    assert errors[0]["code"] == "TS2345"
    assert errors[0]["file"] == str(Path(tmp_path) / "src/index.ts")


def test_tsc_absent_is_skipped(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value=None):
        result = diag.lsp_diagnostics_project(str(tmp_path), tools=("tsc",))

    assert result["available"] is False


def test_lsp_diagnostics_project_all_absent(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value=None):
        result = diag.lsp_diagnostics_project(str(tmp_path))

    assert result["available"] is False
    assert "reason" in result


# ---------------------------------------------------------------------------
# Regression: a non-zero exit with NOTHING parsed must be treated as a
# checker failure (-> None -> unverified upstream), never as a clean pass.
# Real triggers: cargo run from outside the crate root ("could not find
# Cargo.toml"), tsconfig.json not at project_dir, a Go file outside a module
# — all exit non-zero with empty/unmatched output.
# ---------------------------------------------------------------------------


def test_go_vet_nonzero_exit_nothing_parsed_is_unavailable(tmp_py) -> None:
    """go vet failing to resolve a module (not a real vet finding) must not
    read as a clean pass just because nothing matched the line parser."""
    fp = tmp_py("orphan.go", "package main\n")
    with patch.object(diag, "_find_checker", return_value="/usr/bin/go"):
        with patch.object(
            diag.subprocess, "run",
            return_value=_fake_completed(stderr="go: cannot find main module\n", returncode=1),
        ):
            result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp, tools=("go_vet",))

    assert result["available"] is False, (
        "a non-zero exit with nothing parsed must be unavailable (unverified), "
        f"got: {result}"
    )


def test_cargo_check_nonzero_exit_nothing_parsed_is_unavailable(tmp_path: Path) -> None:
    """cargo run outside a crate root (no Cargo.toml) exits non-zero with
    empty stdout — must not read as 'rust: verified'."""
    with patch.object(diag, "_find_checker", return_value="/usr/bin/cargo"):
        with patch.object(
            diag.subprocess, "run",
            return_value=_fake_completed(stdout="", stderr="error: could not find `Cargo.toml`", returncode=101),
        ):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("cargo",))

    assert result["available"] is False, (
        f"a non-zero cargo exit with nothing parsed must be unavailable, got: {result}"
    )


def test_tsc_nonzero_exit_nothing_parsed_is_unavailable(tmp_path: Path) -> None:
    """tsc failing to even start (tsconfig.json not at project_dir) exits
    non-zero with a line the regex cannot match — must not read as a clean
    pass."""
    with patch.object(diag, "_find_checker", return_value="/usr/bin/tsc"):
        with patch.object(
            diag.subprocess, "run",
            return_value=_fake_completed(
                stdout="error TS5058: The specified path does not exist.\n", returncode=1,
            ),
        ):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("tsc",))

    assert result["available"] is False, (
        f"a non-zero tsc exit with nothing parsed must be unavailable, got: {result}"
    )


# A non-zero exit WITH parsed diagnostics is the normal findings case — the
# guard above must not swallow it. test_go_vet_available_and_broken,
# test_cargo_check_available_and_broken, and test_tsc_available_and_broken
# already cover this (all use returncode=1/non-zero with real findings).


# ---------------------------------------------------------------------------
# go vet: project_dir must be threaded through as cwd (module resolution
# depends on it), and both go vet output shapes must parse.
# ---------------------------------------------------------------------------


def test_go_vet_runs_with_project_dir_as_cwd(tmp_py) -> None:
    fp = tmp_py("clean.go", "package main\n")
    project_dir = str(Path(fp).parent)
    with patch.object(diag, "_find_checker", return_value="/usr/bin/go"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed()) as mock_run:
            diag.lsp_diagnostics(project_dir=project_dir, file_path=fp, tools=("go_vet",))

    assert mock_run.call_args.kwargs.get("cwd") == project_dir, (
        "go vet must run with cwd=project_dir so module resolution matches "
        "the caller's project, not the MCP server's own cwd"
    )


def test_go_vet_load_failure_format_is_parsed(tmp_py) -> None:
    """go's module-load failure format ('vet: ./main.go:6:2: undefined: X')
    must still parse — dropping it silently hides a genuine compile error."""
    fp = tmp_py("bad.go", "package main\n")
    vet_stderr = "vet: ./main.go:6:2: undefined: someUndefinedName\n"
    with patch.object(diag, "_find_checker", return_value="/usr/bin/go"):
        with patch.object(diag.subprocess, "run", return_value=_fake_completed(stderr=vet_stderr, returncode=2)):
            result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp, tools=("go_vet",))

    assert result["available"] is True, f"expected the load-failure line to parse; got: {result}"
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert errors, f"expected the undefined-name finding to survive parsing; got {result['diagnostics']}"
    assert errors[0]["line"] == 6
    assert errors[0]["col"] == 2
    assert "undefined: someUndefinedName" in errors[0]["message"]


# ---------------------------------------------------------------------------
# TimeoutExpired — fail-soft contract's "never hangs" half, asserted for the
# three new checkers (previously untested).
# ---------------------------------------------------------------------------


def test_go_vet_timeout_is_unavailable(tmp_py) -> None:
    fp = tmp_py("slow.go", "package main\n")
    with patch.object(diag, "_find_checker", return_value="/usr/bin/go"):
        with patch.object(
            diag.subprocess, "run",
            side_effect=diag.subprocess.TimeoutExpired(cmd=["go", "vet"], timeout=30.0),
        ):
            result = diag.lsp_diagnostics(project_dir=str(Path(fp).parent), file_path=fp, tools=("go_vet",))

    assert result["available"] is False
    assert result["tools_run"] == []


def test_cargo_check_timeout_is_unavailable(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value="/usr/bin/cargo"):
        with patch.object(
            diag.subprocess, "run",
            side_effect=diag.subprocess.TimeoutExpired(cmd=["cargo", "check"], timeout=120.0),
        ):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("cargo",))

    assert result["available"] is False


def test_tsc_timeout_is_unavailable(tmp_path: Path) -> None:
    with patch.object(diag, "_find_checker", return_value="/usr/bin/tsc"):
        with patch.object(
            diag.subprocess, "run",
            side_effect=diag.subprocess.TimeoutExpired(cmd=["tsc"], timeout=120.0),
        ):
            result = diag.lsp_diagnostics_project(str(tmp_path), tools=("tsc",))

    assert result["available"] is False


# --- checker resolution ---------------------------------------------------


def test_the_checked_project_s_venv_wins_over_everything_else(tmp_path, monkeypatch):
    """Found by vise's own `validate` gate blocking on errors that were not real.

    The gate runs inside the MCP server, whose cwd and venv have nothing to do
    with the repo being validated. Resolving the checker from either of those
    picks a mypy that cannot see the project's dependencies, and it reports
    `Cannot find implementation or library stub for "numpy"` about a numpy the
    project has installed. Three such errors were blocking the gate — which is
    the exact failure CLAUDE.md warns about for `pytest`, in a validator vise
    ships.
    """
    from vise.engines.lsp_diagnostics import _find_checker

    project = tmp_path / "proj"
    binary = project / ".venv" / "bin"
    binary.mkdir(parents=True)
    (binary / "mypy").write_text("#!/bin/sh\n", encoding="utf-8")
    (binary / "mypy").chmod(0o755)

    other = tmp_path / "other"
    (other / "bin").mkdir(parents=True)
    (other / "bin" / "mypy").write_text("#!/bin/sh\n", encoding="utf-8")
    (other / "bin" / "mypy").chmod(0o755)
    monkeypatch.setenv("VIRTUAL_ENV", str(other))

    assert _find_checker("mypy", str(project)) == str(binary / "mypy")


def test_without_a_project_the_active_venv_still_wins_over_path(tmp_path, monkeypatch):
    from vise.engines.lsp_diagnostics import _find_checker

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "ruff").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv / "bin" / "ruff").chmod(0o755)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))

    assert _find_checker("ruff") == str(venv / "bin" / "ruff")


def test_an_absent_checker_resolves_to_none(tmp_path, monkeypatch):
    """A missing checker must report absent, not crash — `quality_check` skips
    on it, and a crash there would take the traverse down."""
    from vise.engines.lsp_diagnostics import _find_checker

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert _find_checker("no-such-checker-anywhere", str(tmp_path)) is None
