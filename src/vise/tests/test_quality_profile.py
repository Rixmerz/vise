"""Tests for quality_profile.resolve_check and validators.QualityCheckValidator.

Coverage: every branch of the resolution cascade (no profile / key absent /
binary missing / real run), malformed inputs (bad YAML, non-mapping `checks`,
string vs list vs empty-list values), the VISE_QUALITY_PROFILE override, a
missing `check:` failing closed, and mechanical-vs-asserted source tagging.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from vise.engines.quality_profile import (
    QUALITY_PROFILE_ENV,
    UnboundCheck,
    UnboundReason,
    resolve_check,
)
from vise.engines.validators import QualityCheckValidator, build_validators


def _goal(project_dir: str) -> SimpleNamespace:
    return SimpleNamespace(id="test-quality", project_dir=project_dir, goal="test")


def _write_profile(tmp_path: Path, content: str) -> None:
    vise_dir = tmp_path / ".vise"
    vise_dir.mkdir(parents=True, exist_ok=True)
    (vise_dir / "quality.yaml").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# resolve_check — the four cascade branches + malformed-input degradation
# ---------------------------------------------------------------------------


def test_no_profile_file(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    result = resolve_check(tmp_path, "sast")
    assert result == UnboundCheck(reason=UnboundReason.NO_PROFILE)


def test_profile_exists_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks:\n  unit: [\"pytest\", \"-q\"]\n")
    result = resolve_check(tmp_path, "sast")
    assert result == UnboundCheck(reason=UnboundReason.NOT_LISTED)


def test_configured_binary_present_resolves_command(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks:\n  unit: [\"pytest\", \"-q\"]\n")
    result = resolve_check(tmp_path, "unit")
    assert result == ("pytest", "-q")


def test_malformed_yaml_degrades_to_no_profile(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks: [unclosed\n  - bad")
    result = resolve_check(tmp_path, "sast")
    assert result == UnboundCheck(reason=UnboundReason.NO_PROFILE)


def test_checks_not_a_mapping_degrades_to_no_profile(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks: [\"pytest\", \"-q\"]\n")
    result = resolve_check(tmp_path, "sast")
    assert result == UnboundCheck(reason=UnboundReason.NO_PROFILE)


def test_string_value_is_shlex_split(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, 'checks:\n  unit: "pytest -q"\n')
    result = resolve_check(tmp_path, "unit")
    assert result == ("pytest", "-q")


def test_unparseable_shlex_string_degrades_to_not_listed(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks:\n  unit: \"pytest 'x\"\n")
    result = resolve_check(tmp_path, "unit")
    assert result == UnboundCheck(reason=UnboundReason.NOT_LISTED)


def test_empty_list_value_is_not_listed(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks:\n  unit: []\n")
    result = resolve_check(tmp_path, "unit")
    assert result == UnboundCheck(reason=UnboundReason.NOT_LISTED)


def test_unreadable_file_degrades_to_no_profile(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    vise_dir = tmp_path / ".vise"
    vise_dir.mkdir(parents=True, exist_ok=True)
    # A directory named quality.yaml: read_text raises IsADirectoryError.
    (vise_dir / "quality.yaml").mkdir()
    result = resolve_check(tmp_path, "sast")
    assert result == UnboundCheck(reason=UnboundReason.NO_PROFILE)


def test_env_var_override(tmp_path, monkeypatch):
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    profile = other_dir / "quality.yaml"
    profile.write_text('checks:\n  unit: ["pytest", "-q"]\n', encoding="utf-8")
    monkeypatch.setenv(QUALITY_PROFILE_ENV, str(profile))
    # project_dir itself has no .vise/quality.yaml at all.
    result = resolve_check(tmp_path, "unit")
    assert result == ("pytest", "-q")


# ---------------------------------------------------------------------------
# QualityCheckValidator — cascade through the validator + evidence strings
# ---------------------------------------------------------------------------


def test_validator_no_profile_skips_asserted(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "asserted"
    assert rec.confidence_contribution == v.weight
    assert rec.evidence == (
        "quality check 'sast' not configured — create .vise/quality.yaml with a `checks:` map"
    )
    assert rec.outcome == "unverified"


def test_validator_key_absent_skips_asserted(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks:\n  unit: [\"pytest\", \"-q\"]\n")
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "asserted"
    assert rec.evidence == (
        "quality check 'sast' not configured — add `sast:` to checks: in .vise/quality.yaml"
    )
    assert rec.outcome == "unverified"


def test_validator_unparseable_command_skips_asserted(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, "checks:\n  unit: \"pytest 'x\"\n")
    v = QualityCheckValidator(check="unit")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "asserted"
    assert rec.evidence == (
        "quality check 'unit' not configured — add `unit:` to checks: in .vise/quality.yaml"
    )
    assert rec.outcome == "unverified"


def test_validator_binary_missing_skips_asserted(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, 'checks:\n  sast: ["vise-no-such-binary-xyz", "-r", "src"]\n')
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "asserted"
    assert rec.evidence == "quality check 'sast' skipped — vise-no-such-binary-xyz not on PATH"
    assert rec.outcome == "unverified"


def test_validator_real_run_pass_is_mechanical(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    cmd = f'["{sys.executable}", "-c", "raise SystemExit(0)"]'
    _write_profile(tmp_path, f"checks:\n  sast: {cmd}\n")
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "mechanical"
    assert rec.exit_code == 0
    assert rec.outcome == "verified", "a real run that exited 0 is a verified pass"


def test_validator_real_run_fail_is_mechanical(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    cmd = f'["{sys.executable}", "-c", "raise SystemExit(3)"]'
    _write_profile(tmp_path, f"checks:\n  sast: {cmd}\n")
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is False
    assert rec.source == "mechanical"
    assert rec.exit_code == 3
    assert rec.confidence_contribution == 0.0


def test_missing_check_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    # No `check:` given at all — a misconfiguration, distinct from an unbound
    # check. build_validators must not crash (no try/except there): `check`
    # carries a default, and the resulting validator fails closed at run().
    validators = build_validators([{"type": "quality_check"}])
    assert len(validators) == 1
    rec = validators[0].run(_goal(str(tmp_path)))
    assert rec.passed is False
    assert rec.source == "mechanical"


def test_build_validators_no_profile_does_not_block_traversal(tmp_path, monkeypatch):
    """Acceptance #4: a workflow node declaring quality_check on a machine
    with no .vise/quality.yaml must not block traversal.
    """
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    validators = build_validators([{"type": "quality_check", "check": "sast"}])
    rec = validators[0].run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "asserted"


# ---------------------------------------------------------------------------
# project-relative commands
#
# The pre-flight existence check and the subprocess must agree on a working
# directory. They did not: shutil.which() resolved a path-like command against
# the MCP SERVER's cwd while the command ran with cwd=project_dir. So every
# relative command skip-passed forever with evidence reading "not on PATH" —
# including node_modules/.bin/eslint, which is how essentially every JS repo
# invokes its linter. A green gate that never ran anything is the exact failure
# this validator's source="asserted" tagging exists to make visible, and this
# bug produced it while claiming the tool was missing.
# ---------------------------------------------------------------------------


def _write_script(path: Path, exit_code: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)


def test_project_relative_command_runs_from_a_foreign_cwd(tmp_path, monkeypatch):
    """The regression itself: chdir somewhere else, the check must still run."""
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_script(tmp_path / "scripts" / "check.sh", exit_code=0)
    _write_profile(tmp_path, 'checks:\n  sast: ["scripts/check.sh"]\n')

    # The MCP server's cwd is wherever Claude Code launched, never guaranteed
    # to be the project. tmp_path is the one directory it is certainly not.
    monkeypatch.chdir(tmp_path.parent)

    rec = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))

    assert rec.source == "mechanical", f"skip-passed instead of running: {rec.evidence}"
    assert rec.passed is True
    assert rec.exit_code == 0


def test_project_relative_command_that_fails_blocks_the_gate(tmp_path, monkeypatch):
    """And it must be able to go RED — skip-passing hid failures, not just runs."""
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_script(tmp_path / "scripts" / "check.sh", exit_code=1)
    _write_profile(tmp_path, 'checks:\n  sast: ["scripts/check.sh"]\n')
    monkeypatch.chdir(tmp_path.parent)

    rec = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))

    assert rec.passed is False
    assert rec.source == "mechanical"
    assert rec.exit_code == 1


def test_missing_relative_command_skips_and_says_where_it_looked(tmp_path, monkeypatch):
    """Still fail-open when the path really is absent — with accurate evidence.

    "not on PATH" was a lie for a relative command: PATH was never consulted.
    """
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, 'checks:\n  sast: ["scripts/absent.sh"]\n')

    rec = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))

    assert rec.passed is True
    assert rec.source == "asserted"
    assert "not found in the project" in rec.evidence


def test_a_non_executable_relative_file_is_not_treated_as_runnable(tmp_path, monkeypatch):
    """Existence is not enough — subprocess would raise OSError on chmod 644."""
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    script = tmp_path / "scripts" / "check.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o644)
    _write_profile(tmp_path, 'checks:\n  sast: ["scripts/check.sh"]\n')

    rec = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))

    assert rec.passed is True
    assert rec.source == "asserted"


def test_bare_command_names_still_resolve_through_path(tmp_path, monkeypatch):
    """The fix must not break the common case: a bare name is a PATH lookup."""
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, 'checks:\n  sast: ["sh", "-c", "exit 0"]\n')

    rec = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))

    assert rec.source == "mechanical"
    assert rec.passed is True


def test_absolute_command_paths_still_resolve(tmp_path, monkeypatch):
    """Path(project_dir) / "/abs" collapses to "/abs" — assert it, don't assume."""
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, f'checks:\n  sast: ["{sys.executable}", "-c", "pass"]\n')

    rec = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))

    assert rec.source == "mechanical"
    assert rec.passed is True


# ---------------------------------------------------------------------------
# record naming — which check went red, not just that one did
# ---------------------------------------------------------------------------


def test_records_are_named_for_the_check_not_the_validator_type(tmp_path, monkeypatch):
    """`security` declares sast+sca+secrets+contracts. A failed[] entry reading
    "quality_check" told you a gate blocked and nothing about which one."""
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, 'checks:\n  sast: ["sh", "-c", "exit 1"]\n')

    ran = QualityCheckValidator(check="sast").run(_goal(str(tmp_path)))
    skipped = QualityCheckValidator(check="secrets").run(_goal(str(tmp_path)))

    assert ran.name == "quality_check:sast"
    assert skipped.name == "quality_check:secrets"


@pytest.fixture(autouse=True)
def _trust_repo_commands(monkeypatch):
    """These tests are about the resolution cascade, not about consent.

    A repo-declared command now runs only once approved on this machine
    (``vise.core.consent``); ``test_consent.py`` owns that behaviour. Here the
    blanket trust keeps every case below measuring what it was written to.
    """
    monkeypatch.setenv("VISE_TRUST_PROJECT_TOOLS", "1")
