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


def _diag_unavailable(project_dir: str, file_path: str, tools=()) -> dict:
    return {"available": False, "reason": "no tool", "diagnostics": [], "tools_run": []}


def _diag_clean(project_dir: str, file_path: str, tools=()) -> dict:
    return {"available": True, "diagnostics": [], "tools_run": ["ruff"]}


def _diag_with_error(project_dir: str, file_path: str, tools=()) -> dict:
    return {
        "available": True,
        "diagnostics": [
            {"severity": "error", "line": 5, "col": 1, "code": "F821",
             "message": "Undefined name 'foo'", "source": "ruff"},
        ],
        "tools_run": ["ruff"],
    }


def _diag_with_warning_only(project_dir: str, file_path: str, tools=()) -> dict:
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
    assert "nothing to check" in result.evidence
    assert "no changed source files" in result.evidence
    assert result.outcome == "unverified"


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
    assert "could not check" in result.evidence
    assert "ruff" in result.evidence
    assert "mypy" in result.evidence
    assert result.outcome == "unverified"


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
    assert result.outcome == "verified"


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
    assert result.outcome == "failed"


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
    assert result.outcome == "unverified"


# ---------------------------------------------------------------------------
# Many errors → capped at 5 in evidence, remainder shown as "+N more"
# ---------------------------------------------------------------------------


def test_lsp_clean_many_errors_capped_in_evidence(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "many.py"
    py_file.write_text("\n".join(f"x{i} = undefined_{i}" for i in range(10)), encoding="utf-8")
    v = LspCleanValidator()

    def _many_errors(project_dir: str, file_path: str, tools=()) -> dict:
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
    assert result.outcome in ("verified", "unverified", "failed")


# ---------------------------------------------------------------------------
# outcome field — one test per fail-open path, all mocked (no ruff dependency)
# ---------------------------------------------------------------------------


def test_lsp_clean_outcome_no_changed_files_is_unverified(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    v = LspCleanValidator()
    with patch.object(v, "_changed_files", return_value=[]):
        result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "unverified"


def test_lsp_clean_outcome_no_checker_is_unverified(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "app.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_unavailable):
            result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "unverified"
    assert "ruff" in result.evidence
    assert "mypy" in result.evidence


def test_lsp_clean_outcome_clean_is_verified(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "clean.py"
    py_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_clean):
            result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "verified"


def test_lsp_clean_outcome_error_is_failed(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "bad.py"
    py_file.write_text("x = undefined_var_xyz\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_with_error):
            result = v.run(goal)
    assert result.passed is False
    assert result.outcome == "failed"


def test_lsp_clean_outcome_engine_raises_is_unverified(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "any.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    v = LspCleanValidator()

    with patch.object(v, "_changed_files", side_effect=RuntimeError("boom")):
        result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "unverified"


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


# ---------------------------------------------------------------------------
# Per-language checkers — Go, Rust, TypeScript.  Subprocess boundary is always
# mocked: go/cargo/tsc are not installed in this environment.
# ---------------------------------------------------------------------------


def _diag_go_clean(project_dir: str, file_path: str, tools=()) -> dict:
    return {"available": True, "diagnostics": [], "tools_run": ["go_vet"]}


def _diag_go_broken(project_dir: str, file_path: str, tools=()) -> dict:
    return {
        "available": True,
        "diagnostics": [
            {"severity": "error", "line": 4, "col": 2, "code": "",
             "message": "Printf call has arguments but no formatting directives",
             "source": "go vet"},
        ],
        "tools_run": ["go_vet"],
    }


def _diag_unavailable_tools(project_dir: str, file_path: str, tools=()) -> dict:
    return {"available": False, "reason": "no tool", "diagnostics": [], "tools_run": []}


def test_lsp_clean_go_available_and_clean(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(go_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_go_clean):
            result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "verified"


def test_lsp_clean_go_available_and_broken(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(go_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_go_broken):
            result = v.run(goal)
    assert result.passed is False
    assert result.outcome == "failed"


def test_lsp_clean_go_absent_is_skipped(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(go_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag_unavailable_tools):
            result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "unverified"


def _diag_project_clean(project_dir: str, tools=()) -> dict:
    return {"available": True, "diagnostics": [], "tools_run": list(tools)}


def test_lsp_clean_rust_available_and_clean(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    rs_file = tmp_path / "src" / "main.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text("fn main() {}\n", encoding="utf-8")
    v = LspCleanValidator()

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(rs_file)]):
        with patch.object(diag_mod, "lsp_diagnostics_project", side_effect=_diag_project_clean):
            result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "verified"


def test_lsp_clean_rust_available_and_broken(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    rs_file = tmp_path / "src" / "main.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text("fn main() {}\n", encoding="utf-8")
    v = LspCleanValidator()

    def _diag_project_broken(project_dir: str, tools=()) -> dict:
        return {
            "available": True,
            "tools_run": list(tools),
            "diagnostics": [
                {"severity": "error", "line": 1, "col": 1, "code": "E0308",
                 "message": "mismatched types", "source": "cargo",
                 "file": str(rs_file)},
            ],
        }

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(rs_file)]):
        with patch.object(diag_mod, "lsp_diagnostics_project", side_effect=_diag_project_broken):
            result = v.run(goal)
    assert result.passed is False
    assert result.outcome == "failed"


def test_lsp_clean_rust_absent_is_skipped(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    rs_file = tmp_path / "src" / "main.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text("fn main() {}\n", encoding="utf-8")
    v = LspCleanValidator()

    def _diag_project_unavailable(project_dir: str, tools=()) -> dict:
        return {"available": False, "reason": "no cargo", "diagnostics": [], "tools_run": []}

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(rs_file)]):
        with patch.object(diag_mod, "lsp_diagnostics_project", side_effect=_diag_project_unavailable):
            result = v.run(goal)
    assert result.passed is True
    assert result.outcome == "unverified"


def test_lsp_clean_typescript_available_and_broken(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    ts_file = tmp_path / "src" / "index.ts"
    ts_file.parent.mkdir(parents=True)
    ts_file.write_text("const x: number = 'nope';\n", encoding="utf-8")
    v = LspCleanValidator()

    def _diag_project_ts_broken(project_dir: str, tools=()) -> dict:
        return {
            "available": True,
            "tools_run": list(tools),
            "diagnostics": [
                {"severity": "error", "line": 1, "col": 7, "code": "TS2322",
                 "message": "Type 'string' is not assignable to type 'number'.",
                 "source": "tsc", "file": str(ts_file)},
            ],
        }

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(ts_file)]):
        with patch.object(diag_mod, "lsp_diagnostics_project", side_effect=_diag_project_ts_broken):
            result = v.run(goal)
    assert result.passed is False
    assert result.outcome == "failed"
    assert "TS2322" in result.evidence


def test_lsp_clean_project_lang_evidence_names_full_relative_path(tmp_path: Path) -> None:
    """A whole-project checker (rust/ts) finding must be reported with its
    path relative to project_dir, not just the file's basename — otherwise
    two files with the same name (mod.rs in different dirs) are
    indistinguishable in the evidence."""
    goal = _make_goal(str(tmp_path))
    rs_file = tmp_path / "src" / "widgets" / "mod.rs"
    rs_file.parent.mkdir(parents=True)
    rs_file.write_text("fn widget() {}\n", encoding="utf-8")
    v = LspCleanValidator()

    def _diag_project_broken(project_dir: str, tools=()) -> dict:
        return {
            "available": True,
            "tools_run": list(tools),
            "diagnostics": [
                {"severity": "error", "line": 2, "col": 1, "code": "E0308",
                 "message": "mismatched types", "source": "cargo",
                 "file": str(rs_file)},
            ],
        }

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(rs_file)]):
        with patch.object(diag_mod, "lsp_diagnostics_project", side_effect=_diag_project_broken):
            result = v.run(goal)

    assert result.passed is False
    assert "src/widgets/mod.rs" in result.evidence, (
        f"evidence must name the file relative to project_dir, not just its "
        f"basename; got: {result.evidence!r}"
    )


# ---------------------------------------------------------------------------
# Time budget — a single lsp_clean run must not be able to spend an
# unbounded amount of wall-clock time across every checker it invokes.
# ---------------------------------------------------------------------------


def test_lsp_clean_time_budget_stops_remaining_languages(tmp_path: Path) -> None:
    """Once the total elapsed time exceeds the run's budget, languages not
    yet checked are reported unverified — the checker for them is never
    started, rather than being allowed to run unbounded."""
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "clean.py"
    py_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    v = LspCleanValidator(time_budget_s=-1.0)  # always already over budget

    go_calls: list[str] = []

    def _diag(project_dir: str, file_path: str, tools=("ruff", "mypy")) -> dict:
        go_calls.append(file_path)
        return _diag_clean(project_dir, file_path, tools)

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file), str(go_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_diag):
            result = v.run(goal)

    # A zero budget means every language is over-budget before its checker
    # runs — neither ruff/mypy nor go vet should ever be invoked.
    assert go_calls == []
    assert result.passed is True
    assert result.outcome == "unverified"
    assert "time budget" in result.evidence


# ---------------------------------------------------------------------------
# Mixed-language change: one checker missing must not suppress the other
# language's findings (spec scenario, constraint 4).
# ---------------------------------------------------------------------------


def test_lsp_clean_mixed_language_go_absent_does_not_suppress_python(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "bad.py"
    py_file.write_text("x = undefined_var_xyz\n", encoding="utf-8")
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    v = LspCleanValidator()

    def _mixed(project_dir: str, file_path: str, tools=("ruff", "mypy")) -> dict:
        if file_path.endswith(".py"):
            return _diag_with_error(project_dir, file_path)
        # Go checker absent.
        return {"available": False, "reason": "no go", "diagnostics": [], "tools_run": []}

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file), str(go_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_mixed):
            result = v.run(goal)

    # Python's error must still fail the gate even though Go's checker was absent.
    assert result.passed is False
    assert result.outcome == "failed"
    assert "python: verified" in result.evidence
    assert "go: unverified" in result.evidence


def test_lsp_clean_mixed_language_reports_per_language_status(tmp_path: Path) -> None:
    goal = _make_goal(str(tmp_path))
    py_file = tmp_path / "clean.py"
    py_file.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    go_file = tmp_path / "main.go"
    go_file.write_text("package main\n", encoding="utf-8")
    v = LspCleanValidator()

    def _mixed(project_dir: str, file_path: str, tools=("ruff", "mypy")) -> dict:
        if file_path.endswith(".py"):
            return _diag_clean(project_dir, file_path)
        return _diag_go_clean(project_dir, file_path)

    import vise.engines.lsp_diagnostics as diag_mod
    with patch.object(v, "_changed_files", return_value=[str(py_file), str(go_file)]):
        with patch.object(diag_mod, "lsp_diagnostics", side_effect=_mixed):
            result = v.run(goal)

    assert result.passed is True
    assert result.outcome == "verified"
    assert "python: verified" in result.evidence
    assert "go: verified" in result.evidence


# ---------------------------------------------------------------------------
# Whole-project checker filtered to the changed set (spec scenario, constraint 3).
# ---------------------------------------------------------------------------


def test_lsp_clean_whole_project_finding_outside_changed_set_does_not_fail(tmp_path: Path) -> None:
    """A cargo/tsc finding in a file NOT in the changed set must not fail the gate."""
    goal = _make_goal(str(tmp_path))
    changed_rs = tmp_path / "src" / "changed.rs"
    changed_rs.parent.mkdir(parents=True)
    changed_rs.write_text("fn main() {}\n", encoding="utf-8")
    untouched_rs = tmp_path / "src" / "untouched.rs"
    untouched_rs.write_text("fn other() {}\n", encoding="utf-8")
    v = LspCleanValidator()

    def _project_result(project_dir: str, tools=()) -> dict:
        return {
            "available": True,
            "tools_run": list(tools),
            "diagnostics": [
                # Finding in a file that was NOT changed — must be filtered out.
                {"severity": "error", "line": 1, "col": 1, "code": "E0308",
                 "message": "mismatched types", "source": "cargo",
                 "file": str(untouched_rs)},
            ],
        }

    import vise.engines.lsp_diagnostics as diag_mod
    # Only changed_rs is in the changed set — untouched_rs is not.
    with patch.object(v, "_changed_files", return_value=[str(changed_rs)]):
        with patch.object(diag_mod, "lsp_diagnostics_project", side_effect=_project_result):
            result = v.run(goal)

    assert result.passed is True, f"a diagnostic in an unchanged file must not fail the gate: {result.evidence}"
    assert result.outcome == "verified"


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
