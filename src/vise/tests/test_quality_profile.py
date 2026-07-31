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


def test_validator_binary_missing_skips_asserted(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    _write_profile(tmp_path, 'checks:\n  sast: ["vise-no-such-binary-xyz", "-r", "src"]\n')
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "asserted"
    assert rec.evidence == "quality check 'sast' skipped — vise-no-such-binary-xyz not on PATH"


def test_validator_real_run_pass_is_mechanical(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALITY_PROFILE_ENV, raising=False)
    cmd = f'["{sys.executable}", "-c", "raise SystemExit(0)"]'
    _write_profile(tmp_path, f"checks:\n  sast: {cmd}\n")
    v = QualityCheckValidator(check="sast")
    rec = v.run(_goal(str(tmp_path)))
    assert rec.passed is True
    assert rec.source == "mechanical"
    assert rec.exit_code == 0


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
