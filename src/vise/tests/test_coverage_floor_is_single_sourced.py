"""One floor, in one file.

The ratchet existed in four copies with four different numbers: CI passed
`--fail-under=70`, `.vise/quality.yaml` passed 71, CLAUDE.md documented 74, and
the measured total was higher than all of them. "The coverage gate" therefore
meant a different number depending on which command you ran, and the one that
actually gated a merge was the lowest.

A ratchet nobody can state the value of is not a ratchet. These tests make
adding a second copy fail rather than drift.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PYPROJECT = REPO / "pyproject.toml"
CALLERS = ("[.]github/workflows/ci.yml", ".vise/quality.yaml", "CLAUDE.md")


def _config() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_the_floor_is_declared_in_pyproject():
    report = _config()["tool"]["coverage"]["report"]
    assert isinstance(report.get("fail_under"), int), (
        "fail_under must be set in [tool.coverage.report] — it is the only "
        "place every caller reads without being told to"
    )


@pytest.mark.parametrize("name", [".github/workflows/ci.yml",
                                  ".vise/quality.yaml", "CLAUDE.md"])
def test_no_caller_hardcodes_a_second_floor(name):
    path = REPO / name
    assert path.exists(), f"{name} is part of this repo; its absence is the finding"
    found = re.findall(r"--fail-under=(\d+)", path.read_text(encoding="utf-8"))
    assert not found, (
        f"{name} passes --fail-under={found}, which overrides the one in "
        f"pyproject.toml. The floor belongs in exactly one file"
    )


def test_every_caller_combines_before_reporting():
    """Reporting without combining measures the parent process only.

    Every hook is tested as a subprocess, so without `coverage combine` the
    whole `hooks/` package reports 0% — which is the reverse of the truth and
    would drag any honest floor down with it.
    """
    for name in (".github/workflows/ci.yml", ".vise/quality.yaml", "CLAUDE.md"):
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "coverage report" not in text:
            continue
        assert "coverage combine" in text, (
            f"{name} reports coverage without combining first"
        )


def test_subprocess_instrumentation_is_configured():
    """The three pieces that make a launched hook measurable."""
    run = _config()["tool"]["coverage"]["run"]
    assert run.get("parallel") is True, "parallel data files, one per process"
    assert (Path(__file__).parent / "sitecustomize.py").exists(), (
        "the shim every child interpreter imports at startup"
    )
    conftest = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert "COVERAGE_PROCESS_START" in conftest, (
        "conftest must export what sitecustomize looks for"
    )
