"""A check that is killed by its own clock reports the wrong thing.

`QualityCheckValidator` defaults to a 120s timeout. vise's own instrumented
suite takes about 90s — 75% of the budget on unloaded hardware, and a suite is
the one check whose runtime only grows. What happens past the ceiling is the
part that matters: `unit` fails on a timeout, and the `coverage` check that
follows does not fail. It reads whatever `.coverage` data is on disk from some
earlier run and grades that, so a red suite can produce a green coverage number
describing a run that never finished.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.graph_parser import load_graph_from_file
from vise.engines.validators import QualityCheckValidator, build_validators

WORKFLOWS = Path(__file__).resolve().parents[1] / "assets" / "workflows"

#: What a full instrumented run of this repo costs, with room for a loaded
#: machine. Raise it when the suite genuinely gets slower; do not lower it to
#: make a timeout fit.
MEASURED_SUITE_SECONDS = 90


def _validators_for(graph_name: str, node_id: str):
    graph = load_graph_from_file(WORKFLOWS / graph_name)
    return build_validators(graph.nodes[node_id].validators)


def test_the_unit_check_timeout_exceeds_this_repos_own_suite():
    unit = [
        v for v in _validators_for("quality-gate-graph.yaml", "tests")
        if isinstance(v, QualityCheckValidator) and v.check == "unit"
    ]
    assert unit, "the tests node must gate on the unit check"
    assert unit[0].timeout > MEASURED_SUITE_SECONDS * 2, (
        f"the unit check gets {unit[0].timeout}s for a suite that takes "
        f"{MEASURED_SUITE_SECONDS}s here — the first project slower than this "
        f"one gets a timeout reported as a failing suite"
    )


def test_a_declared_timeout_reaches_the_validator():
    """It is plumbed through **kwargs, which is easy to break silently."""
    built = build_validators([{"type": "quality_check", "check": "unit",
                               "timeout": 900}])
    assert built[0].timeout == 900


@pytest.mark.parametrize("declared", ["900", 900.0, 900])
def test_a_timeout_from_yaml_is_coerced_to_an_int(declared):
    """A YAML `timeout: "900"` used to reach subprocess.run as a str.

    Which raises TypeError *inside* the check, which a fail-closed validator
    then reports as the check having failed — a false red with a traceback that
    names subprocess rather than the config.
    """
    built = build_validators([{"type": "quality_check", "check": "unit",
                               "timeout": declared}])
    assert built[0].timeout == 900
    assert isinstance(built[0].timeout, int)


def test_a_nonsense_timeout_does_not_crash_the_build():
    built = build_validators([{"type": "quality_check", "check": "unit",
                               "timeout": "soon"}])
    assert built[0].timeout == QualityCheckValidator.timeout
