"""A command the repository chose runs only after someone here approved it.

The deferred finding from the threat pass: ``.vise/quality.yaml`` is committed
with the repository, ``QualityCheckValidator`` runs whatever it names, and the
exit code is graded ``mechanical``. So a clone could run anything at the first
node gate and have it count as verification.

Each test here is a property of the consent record, not of the CLI's wording:
approval is by digest, so a rewritten command loses it; the record is user
scope, so the repository cannot grant it; the validator that meets an
unapproved command reports *asserted / unverified* and names the next step,
never a pass and never a run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from vise.core import consent
from vise.engines.goal_state import Goal
from vise.engines.validators import QualityCheckValidator


def _profile(project: Path, **checks: str) -> None:
    (project / ".vise").mkdir(exist_ok=True)
    body = "checks:\n" + "".join(f"  {k}: {json.dumps(v)}\n" for k, v in checks.items())
    (project / ".vise" / "quality.yaml").write_text(body, encoding="utf-8")


def _goal(project: Path) -> Goal:
    return Goal(
        id="g", project_dir=str(project), goal="g", acceptance_criteria=[],
        target_confidence=0.9, complexity="unknown", status="active",
        started_at="", updated_at="",
    )


@pytest.fixture(autouse=True)
def _no_blanket_trust(monkeypatch):
    monkeypatch.delenv(consent.TRUST_ENV, raising=False)
    monkeypatch.delenv("VISE_QUALITY_PROFILE", raising=False)


# --- the record ------------------------------------------------------------


def test_nothing_is_approved_until_someone_approves_it(tmp_path):
    assert consent.approval_state(tmp_path, "sast", ["true"]) == consent.UNAPPROVED
    assert not consent.trusted(tmp_path, "sast", ["true"])


def test_approval_is_of_the_exact_command(tmp_path):
    consent.approve(tmp_path, "sast", ["bandit", "-r", "src"])

    assert consent.is_approved(tmp_path, "sast", ["bandit", "-r", "src"])
    assert consent.approval_state(tmp_path, "sast", ["bandit", "-r", "."]) == consent.CHANGED
    assert not consent.trusted(tmp_path, "sast", ["bandit", "-r", "."]), (
        "the repository rewrote the command and kept the approval"
    )


def test_argument_boundaries_are_part_of_the_command():
    assert consent.command_digest(["a b", "c"]) != consent.command_digest(["a", "b c"])


def test_approval_is_per_project(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    consent.approve(a, "sast", ["true"])

    assert consent.is_approved(a, "sast", ["true"])
    assert not consent.is_approved(b, "sast", ["true"])


def test_revoke_withdraws_it(tmp_path):
    consent.approve(tmp_path, "sast", ["true"])
    assert consent.revoke(tmp_path, "sast")
    assert not consent.is_approved(tmp_path, "sast", ["true"])
    assert not consent.revoke(tmp_path, "sast"), "revoking twice reported success"


def test_the_record_lives_in_user_scope_not_the_repository(tmp_path):
    from vise.core.paths import data_dir

    repo = tmp_path / "repo"
    repo.mkdir()
    consent.approve(repo, "sast", ["true"])
    path = consent.approvals_path()

    assert path.exists()
    assert path.is_relative_to(data_dir()), "consent must live in vise's user-scope data dir"
    assert not path.is_relative_to(repo), (
        "a consent file the repository could commit is consent it gave itself"
    )
    assert "sast" in json.loads(path.read_text())["projects"][str(repo.resolve())]


def test_a_corrupt_record_reads_as_nothing_approved(tmp_path):
    path = consent.approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert consent.approval_state(tmp_path, "sast", ["true"]) == consent.UNAPPROVED
    consent.approve(tmp_path, "sast", ["true"])  # and writing over it works
    assert consent.is_approved(tmp_path, "sast", ["true"])


def test_the_blanket_env_var_trusts_everything(tmp_path, monkeypatch):
    monkeypatch.setenv(consent.TRUST_ENV, "1")
    assert consent.trusted(tmp_path, "sast", ["anything"])


# --- the validator -----------------------------------------------------------


def test_an_unapproved_command_does_not_run_and_says_what_to_do(tmp_path):
    marker = tmp_path / "ran"
    _profile(tmp_path, sast=f"touch {marker}")

    rec = QualityCheckValidator(check="sast").run(_goal(tmp_path))

    assert not marker.exists(), "the command ran without consent"
    assert rec.passed is True and rec.source == "asserted" and rec.outcome == "unverified"
    assert "vise approve sast" in rec.evidence
    assert "touch" in rec.evidence, "the evidence must show the command being refused"
    assert consent.TRUST_ENV in rec.evidence


def test_an_approved_command_runs_mechanically(tmp_path):
    marker = tmp_path / "ran"
    _profile(tmp_path, sast=f"touch {marker}")
    consent.approve(tmp_path, "sast", ["touch", str(marker)])

    rec = QualityCheckValidator(check="sast").run(_goal(tmp_path))

    assert marker.exists()
    assert rec.source == "mechanical" and rec.exit_code == 0


def test_a_command_rewritten_after_approval_is_refused_and_says_so(tmp_path):
    marker = tmp_path / "ran"
    _profile(tmp_path, sast="true")
    consent.approve(tmp_path, "sast", ["true"])
    _profile(tmp_path, sast=f"touch {marker}")  # the repository moved

    rec = QualityCheckValidator(check="sast").run(_goal(tmp_path))

    assert not marker.exists()
    assert rec.source == "asserted"
    assert "changed since it was approved" in rec.evidence


def test_the_env_var_runs_it_without_a_record(tmp_path, monkeypatch):
    monkeypatch.setenv(consent.TRUST_ENV, "1")
    _profile(tmp_path, sast="true")

    rec = QualityCheckValidator(check="sast").run(_goal(tmp_path))

    assert rec.source == "mechanical"


def test_a_missing_binary_is_still_reported_as_missing_not_unapproved(tmp_path):
    _profile(tmp_path, sast="definitely-not-a-binary-9f3a")

    rec = QualityCheckValidator(check="sast").run(_goal(tmp_path))

    assert rec.source == "asserted"
    assert "not on PATH" in rec.evidence
    assert "vise approve" not in rec.evidence, (
        "asking someone to approve a command that cannot run wastes their consent"
    )


# --- the command -------------------------------------------------------------


def _approve(**kw) -> int:
    from vise.cli.approve_cmd import _cmd_approve

    ns = argparse.Namespace(checks=[], all=False, list=False, revoke=False, project_dir=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return _cmd_approve(ns)


def test_vise_approve_records_the_named_check(tmp_path, capsys):
    _profile(tmp_path, sast="true", lint="ruff check .")

    rc = _approve(checks=["sast"], project_dir=str(tmp_path))

    assert rc == 0
    assert consent.is_approved(tmp_path, "sast", ["true"])
    assert not consent.is_approved(tmp_path, "lint", ["ruff", "check", "."])
    out = capsys.readouterr().out
    assert "sast" in out and "approved" in out and "true" in out, (
        "the command must print the argv being consented to"
    )


def test_vise_approve_all_takes_every_declared_check(tmp_path):
    _profile(tmp_path, sast="true", lint="ruff check .")

    assert _approve(all=True, project_dir=str(tmp_path)) == 0
    assert consent.is_approved(tmp_path, "sast", ["true"])
    assert consent.is_approved(tmp_path, "lint", ["ruff", "check", "."])


def test_vise_approve_list_shows_each_state(tmp_path, capsys):
    _profile(tmp_path, sast="true", lint="ruff check .")
    consent.approve(tmp_path, "sast", ["true"])
    consent.approve(tmp_path, "lint", ["ruff", "check", "src"])

    _approve(list=True, project_dir=str(tmp_path))

    out = capsys.readouterr().out
    assert "approved" in out and "changed" in out


def test_vise_approve_refuses_a_check_the_profile_does_not_declare(tmp_path, capsys):
    _profile(tmp_path, sast="true")

    assert _approve(checks=["secrets"], project_dir=str(tmp_path)) == 1
    assert "cannot approve" in capsys.readouterr().out


def test_vise_approve_with_nothing_named_explains_itself(tmp_path, capsys):
    _profile(tmp_path, sast="true")
    assert _approve(project_dir=str(tmp_path)) == 2
    assert "--all" in capsys.readouterr().out


def test_vise_approve_revoke(tmp_path):
    _profile(tmp_path, sast="true")
    consent.approve(tmp_path, "sast", ["true"])

    assert _approve(checks=["sast"], revoke=True, project_dir=str(tmp_path)) == 0
    assert not consent.is_approved(tmp_path, "sast", ["true"])


# --- bootstrap ---------------------------------------------------------------


def test_bootstrap_approves_what_it_wrote(tmp_path, monkeypatch):
    """The person ran bootstrap; that is the consent. A cloned profile has none."""
    from vise.cli import bootstrap_cmd

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.setattr(bootstrap_cmd, "_resolves", lambda project, cmd: cmd[0] == "ruff")
    monkeypatch.setattr(bootstrap_cmd, "browser_status", lambda: (True, "chromium is available"))

    rc = bootstrap_cmd._cmd_bootstrap(argparse.Namespace(
        project_dir=str(tmp_path), dry_run=False, force=False,
    ))

    assert rc == 0
    state = consent.approved(tmp_path)
    assert state, "bootstrap wrote a profile and approved none of it"
    for check, record in state.items():
        assert record["command"][0] == "ruff", (check, record)
