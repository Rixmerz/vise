"""La puerta que hace que el agente lea por símbolos en vez de por rutas.

Un repo bien factorizado le cuesta *más* a un agente que uno mal factorizado —
más archivos, más saltos, más contexto quemado — así que estructura y
navegabilidad tiran en contra, y el agente resuelve la tensión escribiendo
código acoplado. Las tools de lectura eliminan esa tensión; este hook es lo que
hace que las use en vez de caer en `Read` por costumbre.

Tres cosas que este archivo fija, y que son la diferencia entre una puerta que
la gente conserva y una que apaga a los dos días:

1. **El mensaje trae la llamada de reemplazo ya formada.** Un deny que solo
   dice "no" se rodea: el agente prueba `cat`, después `sed -n`, y al final
   escribe el helper que no pudo encontrar.

2. **El modo warning es el que se usa primero.** Registra lo que HABRÍA negado
   y deja pasar todo, así la tasa de falsos positivos se mide sobre una semana
   real de trabajo antes de que algo bloquee. Esa tasa no se adivina.

3. **El kill switch existe desde la primera línea.** Una puerta que puede
   dejarte afuera de arreglar la puerta se desinstala la primera vez que se
   equivoca — y hacer dogfooding de ésta significa que sus bugs caen sobre la
   persona con menos margen para rodearlos.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "codelayer_gate.py"


def _run(payload: dict, env: dict[str, str], project: Path) -> tuple[dict, str]:
    import os

    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project), **env},
    )
    assert p.returncode == 0, "un hook nunca puede fallar la sesión"
    return json.loads(p.stdout or '{"decision":"approve"}'), p.stderr


def _read(path: str) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


def _bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    return tmp_path


def _denied(out: dict) -> bool:
    return out.get("decision") == "block"


# ---------------------------------------------------------------------------
# el kill switch, primero
# ---------------------------------------------------------------------------

def test_the_gate_is_off_by_default(project: Path):
    out, _ = _run(_read("src/app.py"), {}, project)
    assert not _denied(out)


def test_off_is_inert_even_on_a_path_it_would_deny(project: Path):
    out, err = _run(_read("src/app.py"), {"VISE_CODELAYER": "off"}, project)
    assert not _denied(out)
    assert err.strip() == "", "apagada quiere decir apagada, sin ruido"


def test_an_unknown_mode_falls_back_to_off(project: Path):
    """Un typo en la variable no puede convertirse en una puerta que bloquea."""
    out, _ = _run(_read("src/app.py"), {"VISE_CODELAYER": "enfroce"}, project)
    assert not _denied(out)


# ---------------------------------------------------------------------------
# enforce — lo que bloquea
# ---------------------------------------------------------------------------

ENF = {"VISE_CODELAYER": "enforce"}


@pytest.mark.parametrize("payload", [
    _read("src/app.py"),
    _read("src/deep/nested/service.ts"),
    {"tool_name": "Grep", "tool_input": {"pattern": "foo", "path": "src/"}},
    _bash("cat src/app.py"),
    _bash("sed -n '1,50p' src/app.py"),
    _bash("head -20 src/app.py"),
])
def test_reading_source_by_path_is_denied(project: Path, payload: dict):
    out, _ = _run(payload, ENF, project)
    assert _denied(out)


def test_a_reader_after_a_pipe_is_caught(project: Path):
    """`ls | cat src/x.py` clasificado por el primer token se escapaba."""
    out, _ = _run(_bash("ls && cat src/app.py"), ENF, project)
    assert _denied(out)


def test_an_absolute_path_inside_the_project_is_denied(project: Path):
    out, _ = _run(_read(str(project / "src" / "app.py")), ENF, project)
    assert _denied(out)


# ---------------------------------------------------------------------------
# lo que NUNCA se bloquea — una puerta que traba package.json se apaga en una hora
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "package.json", "pyproject.toml", "go.mod", "Cargo.toml", "uv.lock",
    "tsconfig.json", "README.md", "docs/guia.md", "Makefile", "Dockerfile",
    ".env.example", "migrations/001_init.sql", "src/config.yaml",
    "tests/test_app.py", "src/__tests__/app.spec.ts", "docs/api.txt",
])
def test_non_source_files_are_never_gated(project: Path, path: str):
    out, _ = _run(_read(path), ENF, project)
    assert not _denied(out), f"{path} tiene que poder leerse normalmente"


def test_files_outside_the_scope_are_not_gated(project: Path):
    out, _ = _run(_read("scripts/deploy.py"), ENF, project)
    assert not _denied(out)


def test_the_scope_is_configurable(project: Path):
    env = {**ENF, "VISE_CODELAYER_SCOPE": "lib/,app/"}
    denied, _ = _run(_read("lib/core.py"), env, project)
    allowed, _ = _run(_read("src/core.py"), env, project)
    assert _denied(denied)
    assert not _denied(allowed)


@pytest.mark.parametrize("cmd", [
    "ls -la src/", "git status", "pytest tests/", "npm run build",
    "grep -rn foo docs/", "cat package.json",
])
def test_commands_that_are_not_source_reads_pass(project: Path, cmd: str):
    out, _ = _run(_bash(cmd), ENF, project)
    assert not _denied(out)


# ---------------------------------------------------------------------------
# el mensaje ES la superficie de enseñanza
# ---------------------------------------------------------------------------

def test_the_denial_carries_the_replacement_call(project: Path):
    """Un deny que solo dice "no" se rodea con `cat`, después con `sed`."""
    out, _ = _run(_read("src/payments.py"), ENF, project)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "read_unit(" in reason
    assert "locate(" in reason
    assert "resolve_location(" in reason


def test_the_denial_names_the_file_it_blocked(project: Path):
    out, _ = _run(_read("src/payments.py"), ENF, project)
    assert "src/payments.py" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_denial_names_the_kill_switch(project: Path):
    out, _ = _run(_read("src/app.py"), ENF, project)
    assert "VISE_CODELAYER=off" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_denial_says_what_is_not_gated(project: Path):
    """Sin esto el agente asume que TODO está bloqueado y deja de leer configs."""
    out, _ = _run(_read("src/app.py"), ENF, project)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "tests" in reason and "docs" in reason


def test_both_decision_channels_are_present(project: Path):
    """Mismo motivo que graph_enforcer: uno bloquea, el otro es el que se lee."""
    out, _ = _run(_read("src/app.py"), ENF, project)
    assert out["decision"] == "block"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["message"] == out["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# warn — medir antes de bloquear
# ---------------------------------------------------------------------------

WARN = {"VISE_CODELAYER": "warn"}


def test_warn_lets_everything_through(project: Path):
    out, _ = _run(_read("src/app.py"), WARN, project)
    assert not _denied(out)


def test_warn_records_what_it_would_have_denied(project: Path):
    """La tasa de falsos positivos no se adivina, se observa."""
    _run(_read("src/app.py"), WARN, project)
    _run(_bash("cat src/otro.py"), WARN, project)

    log = project / ".vise" / "codelayer-warnings.jsonl"
    assert log.exists()
    rows = [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    assert len(rows) == 2
    assert {r["tool"] for r in rows} == {"Read", "Bash"}


def test_warn_records_nothing_for_what_it_would_have_allowed(project: Path):
    _run(_read("package.json"), WARN, project)
    assert not (project / ".vise" / "codelayer-warnings.jsonl").exists()


def test_warn_still_explains_itself_on_stderr(project: Path):
    _out, err = _run(_read("src/app.py"), WARN, project)
    assert "read_unit(" in err
    assert "Would deny" in err


# ---------------------------------------------------------------------------
# fail-open — el contrato de todos los hooks de vise
# ---------------------------------------------------------------------------

def test_malformed_input_approves(project: Path):
    import os

    p = subprocess.run(
        [sys.executable, str(HOOK)], input="no es json",
        capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project), **ENF},
    )
    assert p.returncode == 0
    assert json.loads(p.stdout)["decision"] == "approve"


def test_a_missing_project_dir_approves(tmp_path: Path):
    out, _ = _run(_read("src/app.py"), ENF, tmp_path / "no-existe")
    assert isinstance(out.get("decision"), str)


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": None}},
    {"tool_name": "Read", "tool_input": {}},
    {"tool_name": "Read"},
    {},
])
def test_incomplete_payloads_approve(project: Path, payload: dict):
    out, _ = _run(payload, ENF, project)
    assert not _denied(out)


def test_the_hook_stays_within_its_latency_budget(project: Path):
    """§6.7: p95 < 150 ms, o se percibe como "el plugin va lento" y se apaga."""
    import time

    times = []
    for _ in range(10):
        t = time.perf_counter()
        _run(_read("src/app.py"), ENF, project)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    assert times[int(len(times) * 0.9)] < 150, f"p90 {times[int(len(times)*0.9)]:.0f} ms"
