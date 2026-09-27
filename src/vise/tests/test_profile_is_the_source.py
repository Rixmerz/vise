"""One fact, one place: the node gates read the repo's profile, not a copy of it.

`tests_pass`, `tests_fail` and `lint_pass` used to answer "which command runs
this repo's tests" out of `$VISE_TEST_CMD`/`$VISE_LINT_CMD` alone, while
`.vise/quality.yaml` answered the same question for `quality_check`. Two
sources, and `vise bootstrap` wrote both — so editing the profile left a
per-machine copy behind in `.claude/settings.json` that kept winning, silently,
because the env var was consulted first.

The profile is now read directly, under the same consent rule
`QualityCheckValidator` holds: a command that arrived with a clone is the
repository's choice, not this machine's, and does not run until someone here
approves it. These tests pin both halves — that it IS read, and that an
unapproved one is NOT run.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from vise.core import consent
from vise.engines.validators import (
    LintPassValidator,
    TestsFailValidator,
    TestsPassValidator,
    _profile_cmd,
)


def _goal(project_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id="g", project_dir=str(project_dir), goal="x", validator_configs=[]
    )


def _profile(project: Path, body: str) -> None:
    (project / ".vise").mkdir(parents=True, exist_ok=True)
    (project / ".vise" / "quality.yaml").write_text(
        textwrap.dedent(body), encoding="utf-8"
    )


def _script(project: Path, name: str, exit_code: int = 0) -> Path:
    """An executable that exits with *exit_code*, so a validator can really run it."""
    import stat

    path = project / name
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# --- the resolver itself ---------------------------------------------------


def test_no_profile_is_no_command_and_no_complaint(tmp_path: Path) -> None:
    """A repo that never ran bootstrap is the ordinary case, not a problem."""
    cmd, withheld = _profile_cmd(str(tmp_path), "unit")

    assert cmd is None
    assert withheld == ""


def test_a_declared_check_nobody_approved_is_withheld_with_the_remedy(
    tmp_path: Path,
) -> None:
    """A cloned profile is the repository's choice, not this machine's."""
    _profile(tmp_path, """
        checks:
          unit: ["my-runner", "--all"]
    """)

    cmd, withheld = _profile_cmd(str(tmp_path), "unit")

    assert cmd is None
    assert "vise approve unit" in withheld
    assert "my-runner --all" in withheld


def test_an_approved_check_resolves_to_its_command(tmp_path: Path) -> None:
    _profile(tmp_path, """
        checks:
          unit: ["my-runner", "--all"]
    """)
    consent.approve(tmp_path, "unit", ["my-runner", "--all"])

    cmd, withheld = _profile_cmd(str(tmp_path), "unit")

    assert cmd == ("my-runner", "--all")
    assert withheld == ""


def test_trust_env_stands_in_for_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _profile(tmp_path, """
        checks:
          lint: ["my-linter"]
    """)
    monkeypatch.setenv(consent.TRUST_ENV, "1")

    cmd, _ = _profile_cmd(str(tmp_path), "lint")

    assert cmd == ("my-linter",)


# --- tests_pass ------------------------------------------------------------


def test_tests_pass_runs_the_profiles_unit_command(tmp_path: Path) -> None:
    runner = _script(tmp_path, "runner.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          unit: ["{runner}"]
    """)
    consent.approve(tmp_path, "unit", [str(runner)])

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.passed is True
    # Mechanical, not asserted: something actually ran.
    assert record.source == "mechanical"
    assert record.exit_code == 0


def test_tests_pass_reports_a_failing_profile_command_as_a_failure(
    tmp_path: Path,
) -> None:
    runner = _script(tmp_path, "runner.sh", exit_code=1)
    _profile(tmp_path, f"""
        checks:
          unit: ["{runner}"]
    """)
    consent.approve(tmp_path, "unit", [str(runner)])

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.passed is False
    assert record.exit_code == 1


def test_an_unapproved_profile_command_never_runs_and_says_why(
    tmp_path: Path,
) -> None:
    """The consent hole this would otherwise open: a clone executing its own argv."""
    runner = _script(tmp_path, "runner.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          unit: ["{runner}"]
    """)
    # deliberately NOT approved

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.outcome == "unverified"
    assert record.source == "asserted"
    assert "vise approve unit" in record.evidence


def test_the_env_var_still_outranks_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set by hand on this machine, so it is the more deliberate of the two."""
    profile_runner = _script(tmp_path, "profile.sh", exit_code=1)
    env_runner = _script(tmp_path, "env.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          unit: ["{profile_runner}"]
    """)
    consent.approve(tmp_path, "unit", [str(profile_runner)])
    monkeypatch.setenv("VISE_TEST_CMD", str(env_runner))

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.exit_code == 0, "the env var's runner should have run, not the profile's"


def test_an_explicit_node_test_cmd_outranks_everything(tmp_path: Path) -> None:
    profile_runner = _script(tmp_path, "profile.sh", exit_code=1)
    node_runner = _script(tmp_path, "node.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          unit: ["{profile_runner}"]
    """)
    consent.approve(tmp_path, "unit", [str(profile_runner)])

    record = TestsPassValidator(test_cmd=(str(node_runner),)).run(_goal(tmp_path))

    assert record.exit_code == 0


def test_the_profile_outranks_the_venv_guess(tmp_path: Path) -> None:
    """The venv is a guess; the profile is something a person wrote down."""
    import stat

    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR)

    runner = _script(tmp_path, "runner.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          unit: ["{runner}"]
    """)
    consent.approve(tmp_path, "unit", [str(runner)])

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.exit_code == 0


def test_a_project_relative_command_is_found_the_way_it_will_be_run(
    tmp_path: Path,
) -> None:
    """`.venv/bin/python`, `node_modules/.bin/jest` — the usual shape of a profile.

    The pre-check used `shutil.which`, which resolves a relative path against
    the MCP server's cwd, while the command itself runs with cwd=project_dir.
    Every relative command failed the pre-check and skip-passed forever:
    green gate, evidence reading "not on PATH", nothing run.
    """
    import stat

    nested = tmp_path / "tools" / "run-tests"
    nested.parent.mkdir(parents=True)
    nested.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nested.chmod(nested.stat().st_mode | stat.S_IXUSR)

    _profile(tmp_path, """
        checks:
          unit: ["tools/run-tests"]
    """)
    consent.approve(tmp_path, "unit", ["tools/run-tests"])

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.source == "mechanical", record.evidence
    assert record.exit_code == 0


def test_a_missing_relative_command_says_so_in_the_right_words(
    tmp_path: Path,
) -> None:
    """"not on PATH" is the wrong diagnosis for a path that is simply absent."""
    _profile(tmp_path, """
        checks:
          unit: ["tools/nope"]
    """)
    consent.approve(tmp_path, "unit", ["tools/nope"])

    record = TestsPassValidator().run(_goal(tmp_path))

    assert record.outcome == "unverified"
    assert "not found in the project" in record.evidence


# --- tests_fail ------------------------------------------------------------


def test_tests_fail_resolves_the_same_way_tests_pass_does(tmp_path: Path) -> None:
    """Same question ("which command runs this repo's tests"), same answer.

    What it then makes of the result is its own business and unchanged: for a
    runner that is not pytest, a nonzero exit could be a failing test or a
    broken invocation, so it asserts rather than claiming a reproduction. What
    this pins is that the profile's command is the one that ran.
    """
    runner = _script(tmp_path, "runner.sh", exit_code=1)
    _profile(tmp_path, f"""
        checks:
          unit: ["{runner}"]
    """)
    consent.approve(tmp_path, "unit", [str(runner)])

    record = TestsFailValidator().run(_goal(tmp_path))

    assert record.exit_code == 1, "the profile's runner should have run"
    assert record.outcome == "unverified"
    assert "cannot tell a failing test from a broken run" in record.evidence


def test_tests_fail_never_reads_an_unapproved_profile_as_a_reproduction(
    tmp_path: Path,
) -> None:
    """A withheld command must not manufacture a reproduction out of nothing."""
    runner = _script(tmp_path, "runner.sh", exit_code=1)
    _profile(tmp_path, f"""
        checks:
          unit: ["{runner}"]
    """)

    record = TestsFailValidator().run(_goal(tmp_path))

    assert record.outcome == "unverified"
    assert "vise approve unit" in record.evidence


# --- lint_pass -------------------------------------------------------------


def test_lint_pass_runs_the_profiles_lint_command(tmp_path: Path) -> None:
    linter = _script(tmp_path, "linter.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          lint: ["{linter}"]
    """)
    consent.approve(tmp_path, "lint", [str(linter)])

    record = LintPassValidator().run(_goal(tmp_path))

    assert record.passed is True
    assert record.source == "mechanical"


def test_lint_pass_withholds_an_unapproved_command(tmp_path: Path) -> None:
    linter = _script(tmp_path, "linter.sh", exit_code=0)
    _profile(tmp_path, f"""
        checks:
          lint: ["{linter}"]
    """)

    record = LintPassValidator().run(_goal(tmp_path))

    assert record.outcome == "unverified"
    assert "vise approve lint" in record.evidence
