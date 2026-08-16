"""Un gate del que no se puede salir bloquea la sesión, no la protege.

`hooks/goal_gate.py` promete en su docstring que "every escape hatch (max
attempts, plateau, cancel file, override env) is enforced inside
engines.goal_gate", y el mensaje que le imprime al agente cuando bloquea las
nombra una por una.

Corriendo el hook como subproceso real contra un goal activo, tres de las
cuatro sueltan el gate. La cuarta no: `evaluate` libera cuando
`goal.attempts >= VISE_GOAL_GATE_MAX_ATTEMPTS`, pero nada en el proyecto
incrementaba `goal.attempts`. Sesenta invocaciones seguidas y el contador
seguía en cero.

Dos cosas eran falsas a la vez:

  - la salida por tope de intentos no podía dispararse nunca;
  - el mensaje de bloqueo le decía al agente "attempt 0/50" en cada turno, que
    se lee como "el tope no es lo que te tiene acá" por muchas vueltas que
    lleve el loop.

`goal_validate` es el lugar donde avanza: una ronda de validación *es* un
intento, y es la misma llamada que agrega la muestra de confianza que alimenta
la detección de meseta, así que el contador y la ventana quedan sincronizados
por construcción.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines import goal_gate, goal_state
from vise.engines.goal_gate import Action, Decision


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("VISE_GOAL_GATE", "1")
    monkeypatch.delenv("VISE_GOAL_GATE_OVERRIDE", raising=False)
    monkeypatch.delenv("VISE_GOAL_GATE_MAX_ATTEMPTS", raising=False)
    goal_state.set_goal(str(proj), "terminar la feature", target_confidence=0.8)
    return str(proj)


def _continue() -> Decision:
    return Decision(action=Action.CONTINUE, confidence=0.1,
                    target_confidence=0.8, advisory="")


# ---------------------------------------------------------------------------
# las salidas que ya funcionaban — fijadas para que sigan funcionando
# ---------------------------------------------------------------------------

def test_the_gate_blocks_an_unfinished_goal(project: str):
    assert goal_gate.evaluate(project, _continue()).block


def test_the_override_env_var_releases_it(project: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VISE_GOAL_GATE_OVERRIDE", "1")
    gate = goal_gate.evaluate(project, _continue())
    assert not gate.block
    assert gate.cause == "override"


def test_the_cancel_file_releases_it(project: str):
    cancel = goal_gate._cancel_file(project)
    cancel.parent.mkdir(parents=True, exist_ok=True)
    cancel.touch()
    gate = goal_gate.evaluate(project, _continue())
    assert not gate.block
    assert gate.cause == "cancelled"


def test_removing_the_cancel_file_makes_it_block_again(project: str):
    cancel = goal_gate._cancel_file(project)
    cancel.parent.mkdir(parents=True, exist_ok=True)
    cancel.touch()
    assert not goal_gate.evaluate(project, _continue()).block
    cancel.unlink()
    assert goal_gate.evaluate(project, _continue()).block


def test_the_gate_is_off_unless_explicitly_enabled(
    project: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("VISE_GOAL_GATE", raising=False)
    gate = goal_gate.evaluate(project, _continue())
    assert not gate.block
    assert gate.cause == "gate_disabled"


# ---------------------------------------------------------------------------
# la salida que estaba muerta
# ---------------------------------------------------------------------------

def test_the_max_attempts_hatch_releases_the_gate(
    project: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("VISE_GOAL_GATE_MAX_ATTEMPTS", "3")
    goal_state.update_goal(project, attempts=3)

    gate = goal_gate.evaluate(project, _continue())

    assert not gate.block
    assert gate.cause == "max_attempts"


class _FakeMCP:
    """Captura las funciones que `register_goal` decora, para llamarlas directo."""

    def __init__(self) -> None:
        self.registered: dict = {}

    def tool(self, *a, **kw):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


def _goal_validate(project: str) -> dict:
    from vise.tools import goal as goal_tools

    mcp = _FakeMCP()
    goal_tools.register_goal(mcp)
    fn = mcp.registered.get("goal_validate")
    assert fn is not None, "goal_validate no quedó registrado"
    return fn(project_dir=project)


def test_goal_validate_advances_the_attempt_counter(project: str):
    """La regresión: nada incrementaba `attempts`, así que el tope era inalcanzable."""
    assert goal_state.get_goal(project).attempts == 0

    _goal_validate(project)
    assert goal_state.get_goal(project).attempts == 1

    _goal_validate(project)
    assert goal_state.get_goal(project).attempts == 2


def test_enough_validation_rounds_actually_release_the_gate(
    project: str, monkeypatch: pytest.MonkeyPatch
):
    """El recorrido completo: el tope es alcanzable corriendo la herramienta.

    Es la afirmación que importa. Que `evaluate` libere con `attempts=3`
    inyectado a mano no prueba nada si nada del sistema llega a 3.
    """
    monkeypatch.setenv("VISE_GOAL_GATE_MAX_ATTEMPTS", "2")
    assert goal_gate.evaluate(project, _continue()).block

    _goal_validate(project)
    _goal_validate(project)

    gate = goal_gate.evaluate(project, _continue())
    assert not gate.block
    assert gate.cause == "max_attempts"


def test_the_counter_survives_a_reload(project: str):
    """Se persiste al archivo del goal, no vive solo en memoria."""
    goal_state.update_goal(project, attempts=7)
    assert goal_state.get_goal(project).attempts == 7


def test_a_goal_starts_at_zero_attempts(project: str):
    assert goal_state.get_goal(project).attempts == 0


def test_the_block_message_reports_the_real_attempt_count(project: str):
    """El mensaje decía "attempt 0/50" para siempre; ahora sigue al contador."""
    monkey_max = goal_gate._max_attempts()
    goal_state.update_goal(project, attempts=4)

    gate = goal_gate.evaluate(project, _continue())

    assert gate.block
    assert f"attempt 4/{monkey_max}" in gate.reason


def test_the_block_message_still_names_every_hatch_it_honours(project: str):
    """Si el mensaje nombra una salida, esa salida tiene que existir."""
    reason = goal_gate.evaluate(project, _continue()).reason
    assert "goal_abandon" in reason
    assert "goal-cancel" in reason
    assert "VISE_GOAL_GATE_OVERRIDE=1" in reason
