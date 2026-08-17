"""Un bug no está reproducido hasta que un test falla.

`debug-graph.yaml` pide en prosa *"Confirm that at least one test fails"* y no
había forma de expresarlo, así que la puerta más fuerte de todo el workflow de
debug era una frase que el agente decía sobre sí mismo. Un bug no reproducido
pasaba directo a `fix`, que es como una sesión de debugging termina editando
código sobre una hipótesis.

`tests_fail` NO es la negación de `tests_pass`, y esa distinción es todo el
validador. `returncode != 0` es la implementación ingenua y está mal: un
`conftest.py` roto, un flag inválido o un import circular salen distinto de
cero y se ven exactamente igual que una reproducción. Convertir un runner
crashado en "bug reproducido" es la peor dirección posible para equivocarse
acá — habilita la fase de fix sobre evidencia que no existe.

Los códigos de salida de pytest separan las dos cosas y el validador se apoya
en eso:

    0  la suite está verde        -> NO reproducido (falla la puerta)
    1  corrieron y algunos fallan -> reproducido    (única verde)
    2  error de uso               -> unverified
    3  error interno              -> unverified
    4  error de línea de comandos -> unverified
    5  no se recolectó ningún test-> unverified (cero tests no es un test que falla)
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from vise.engines.validators import TestsFailValidator, _REGISTRY, build_validators


@dataclass
class _Goal:
    project_dir: str


@pytest.fixture
def goal(tmp_path: Path) -> _Goal:
    return _Goal(project_dir=str(tmp_path))


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    def run(*a, **kw):
        return subprocess.CompletedProcess(a[0] if a else [], returncode, stdout, stderr)
    return run


# ---------------------------------------------------------------------------
# la única verde
# ---------------------------------------------------------------------------

def test_a_failing_suite_is_a_reproduction(goal, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(1, "1 failed, 12 passed"))
    rec = TestsFailValidator().run(goal)
    assert rec.passed
    assert rec.source == "mechanical"
    assert rec.outcome != "unverified"
    assert "1 failed" in rec.evidence


def test_the_evidence_names_the_failure(goal, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", _fake_run(1, "tests/test_x.py::test_y FAILED\n1 failed")
    )
    assert "1 failed" in TestsFailValidator().run(goal).evidence


# ---------------------------------------------------------------------------
# la roja: no hay nada que arreglar todavía
# ---------------------------------------------------------------------------

def test_a_green_suite_is_not_a_reproduction(goal, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(0, "42 passed"))
    rec = TestsFailValidator().run(goal)
    assert not rec.passed
    assert rec.source == "mechanical"
    assert rec.confidence_contribution == 0.0


# ---------------------------------------------------------------------------
# lo que NO puede contar como reproducción — el corazón del validador
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,que_paso", [
    (2, "error de uso"),
    (3, "error interno"),
    (4, "error de linea de comandos"),
    (5, "no se recolecto ningun test"),
])
def test_a_runner_that_never_ran_tests_is_not_a_reproduction(
    goal, monkeypatch, code: int, que_paso: str
):
    """`returncode != 0` los daría por reproducidos a los cuatro."""
    monkeypatch.setattr(subprocess, "run", _fake_run(code, "", "error"))
    rec = TestsFailValidator().run(goal)
    assert rec.outcome == "unverified", f"{que_paso} se contó como reproducción"
    assert rec.passed, "unverified nunca bloquea — el contrato es fallar abierto"


def test_zero_tests_collected_is_not_a_failing_test(goal, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(5, "no tests ran"))
    assert TestsFailValidator().run(goal).outcome == "unverified"


# ---------------------------------------------------------------------------
# fallar abierto — el contrato de todos los validadores de vise
# ---------------------------------------------------------------------------

def test_a_missing_runner_is_unverified_not_reproduced(goal, monkeypatch):
    """Fabricar una reproducción a partir de un binario ausente sería lo peor."""
    monkeypatch.setattr("shutil.which", lambda _c: None)
    rec = TestsFailValidator().run(goal)
    assert rec.outcome == "unverified"
    assert rec.source == "asserted"
    assert rec.passed, "un runner ausente no puede bloquear el workflow"
    assert "VISE_TEST_CMD" in rec.evidence


def test_an_unknown_runner_asserts_rather_than_claims_a_reproduction(
    goal, monkeypatch
):
    """Sin los códigos de pytest no se puede separar test-falla de invocación-rota."""
    monkeypatch.setenv("VISE_TEST_CMD", "jest --ci")
    monkeypatch.setattr("shutil.which", lambda _c: "/usr/bin/jest")
    monkeypatch.setattr(subprocess, "run", _fake_run(1, "1 failing"))

    rec = TestsFailValidator().run(goal)
    assert rec.outcome == "unverified"
    assert rec.source == "asserted"
    assert "cannot tell" in rec.evidence


def test_the_project_test_command_override_is_honoured(goal, monkeypatch):
    seen: list[list[str]] = []

    def run(cmd, *a, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, "1 failed", "")

    monkeypatch.setenv("VISE_TEST_CMD", "pytest tests/ -x")
    monkeypatch.setattr("shutil.which", lambda _c: "/usr/bin/pytest")
    monkeypatch.setattr(subprocess, "run", run)

    TestsFailValidator().run(goal)
    assert seen[0] == ["pytest", "tests/", "-x"]


def test_an_explicit_yaml_test_cmd_beats_the_env_override(goal, monkeypatch):
    seen: list[list[str]] = []

    def run(cmd, *a, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, "1 failed", "")

    monkeypatch.setenv("VISE_TEST_CMD", "pytest del-entorno")
    monkeypatch.setattr("shutil.which", lambda _c: "/usr/bin/pytest")
    monkeypatch.setattr(subprocess, "run", run)

    TestsFailValidator(test_cmd=("pytest", "del-yaml")).run(goal).evidence
    assert seen[0] == ["pytest", "del-yaml"]


# ---------------------------------------------------------------------------
# cableado
# ---------------------------------------------------------------------------

def test_the_validator_is_registered():
    assert _REGISTRY["tests_fail"] is TestsFailValidator


def test_a_workflow_can_declare_it_by_type():
    built = build_validators([{"type": "tests_fail", "weight": 0.4}])
    assert len(built) == 1
    assert isinstance(built[0], TestsFailValidator)
    assert built[0].weight == 0.4


def test_it_is_not_confused_with_tests_pass():
    from vise.engines.validators import TestsPassValidator

    assert _REGISTRY["tests_pass"] is TestsPassValidator
    assert TestsFailValidator().name == "tests_fail"
