"""`vise bootstrap` — escribir el perfil de calidad, y nada que el repo no pueda correr.

Instalar el plugin trae los agentes, skills, comandos y hooks. Lo que no puede
traer es la parte que es *sobre este repo*: qué comando corre los tests acá,
cuál lintea, qué significa `sast` en un proyecto Go. Eso vive en
`.vise/quality.yaml`, y hasta ahora cada proyecto lo escribía a mano — que
mayormente significaba no escribirlo, así que las puertas skip-pasaban y el
enforcement por el que uno instala vise nunca corría.

La regla que da forma a todo el módulo: **ligar un check solo cuando su
herramienta está de verdad.** Un perfil que nombra `pytest` en un repo sin
pytest no crea rigor, crea una puerta que falla por razones de entorno — y una
puerta que se pone roja cuando no hiciste nada mal es cómo un equipo aprende a
exportar `VISE_NODE_GATE_OVERRIDE=1`, el único hábito que las puertas existen
para prevenir.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from vise.cli import bootstrap_cmd
from vise.cli.bootstrap_cmd import _tool_name, detect, render


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# detección de ecosistema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("manifest,eco", [
    ("pyproject.toml", "python"),
    ("package.json", "node"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
])
def test_the_manifest_names_the_ecosystem(tmp_path: Path, manifest: str, eco: str):
    _write(tmp_path, {manifest: "{}" if manifest.endswith(".json") else ""})
    assert eco in detect(tmp_path)["ecosystems"]


def test_a_polyglot_repo_gets_every_ecosystem(tmp_path: Path):
    _write(tmp_path, {"pyproject.toml": "", "go.mod": "module x\n"})
    assert set(detect(tmp_path)["ecosystems"]) >= {"python", "go"}


def test_an_unrecognised_repo_binds_nothing_and_says_so(tmp_path: Path):
    found = detect(tmp_path)
    assert found["ecosystems"] == []
    out = render(found)
    assert "unrecognised" in out
    assert "checks:" in out


# ---------------------------------------------------------------------------
# la regla central: no ligar lo que no está
# ---------------------------------------------------------------------------

def test_a_missing_tool_is_never_bound(tmp_path: Path, monkeypatch):
    """Nombrar pytest en un repo sin pytest no crea rigor: crea un rojo de entorno."""
    _write(tmp_path, {"pyproject.toml": ""})
    monkeypatch.setattr("shutil.which", lambda _c: None)
    found = detect(tmp_path)
    assert "unit" not in found["bound"]
    assert "unit" in found["skipped"]


def test_a_skipped_check_is_reported_not_hidden(tmp_path: Path, monkeypatch):
    _write(tmp_path, {"go.mod": "module x\n"})
    monkeypatch.setattr("shutil.which", lambda _c: None)
    out = render(detect(tmp_path))
    assert "Not bound" in out
    assert "real gap, not an" in out


def test_python_module_commands_check_the_module_not_the_interpreter(
    tmp_path: Path, monkeypatch
):
    """El bug que costó una corrida en seco: `.venv/bin/python -m mypy` ligaba
    porque el intérprete existía, sin importar si mypy estaba importable."""
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\nexit 1\n")
    (venv / "python").chmod(0o755)
    _write(tmp_path, {"pyproject.toml": ""})

    found = detect(tmp_path)
    for check, cmd in found["bound"].items():
        if cmd[0].endswith("/python"):
            pytest.fail(f"{check} ligó un módulo que el intérprete no puede importar")


# ---------------------------------------------------------------------------
# adopción — instalado no es lo mismo que adoptado
# ---------------------------------------------------------------------------

def test_a_tool_installed_but_unconfigured_is_not_bound(tmp_path: Path, monkeypatch):
    """`mypy .` sobre un repo sin config es una avalancha: rojo por configuración
    faltante, no por código roto."""
    _write(tmp_path, {"pyproject.toml": "[project]\nname='x'\n"})
    monkeypatch.setattr("shutil.which", lambda c: f"/usr/bin/{c}")
    assert "types" not in detect(tmp_path)["bound"]


def test_the_same_tool_is_bound_once_the_repo_configures_it(tmp_path: Path, monkeypatch):
    _write(tmp_path, {"pyproject.toml": "[tool.mypy]\nstrict = true\n"})
    monkeypatch.setattr("shutil.which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setattr("vise.cli.bootstrap_cmd._module_importable", lambda *_a: True)
    assert "types" in detect(tmp_path)["bound"]


def test_a_configless_linter_still_binds_as_a_fallback(tmp_path: Path, monkeypatch):
    """El bug de la primera versión: bloquear el check entero por adopción mataba
    `go vet` y `cargo clippy`, que están diseñados para correr sin config."""
    _write(tmp_path, {"go.mod": "module x\n"})
    monkeypatch.setattr("shutil.which", lambda c: f"/usr/bin/{c}")
    lint = detect(tmp_path)["bound"].get("lint")
    assert lint is not None, "un repo Go sin .golangci.yml se quedó sin linter"
    assert lint[:2] == ["go", "vet"]


def test_eslint_needs_a_config_to_count(tmp_path: Path):
    _write(tmp_path, {
        "package.json": json.dumps({"devDependencies": {"eslint": "^9"}}),
        "node_modules/.bin/eslint": "",
    })
    assert "lint" not in detect(tmp_path)["bound"]

    _write(tmp_path, {"eslint.config.js": "export default []\n"})
    assert "lint" in detect(tmp_path)["bound"]


# ---------------------------------------------------------------------------
# npx no puede instalar nada a mitad de una puerta
# ---------------------------------------------------------------------------

def test_npx_only_counts_when_the_package_is_already_local(tmp_path: Path):
    """Una puerta que instala software no es una puerta."""
    _write(tmp_path, {"package.json": "{}", "eslint.config.js": ""})
    assert "lint" not in detect(tmp_path)["bound"]

    _write(tmp_path, {"node_modules/.bin/eslint": ""})
    assert detect(tmp_path)["bound"]["lint"][:2] == ["npx", "--no-install"]


# ---------------------------------------------------------------------------
# nombres legibles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd,expected", [
    ([".venv/bin/python", "-m", "mypy", "."], "mypy"),
    (["npx", "--no-install", "eslint", "."], "eslint"),
    (["go", "vet", "./..."], "go"),
    (["cargo", "test"], "cargo"),
])
def test_the_tool_name_is_what_a_reader_would_recognise(cmd, expected):
    assert _tool_name(cmd) == expected


# ---------------------------------------------------------------------------
# el archivo generado
# ---------------------------------------------------------------------------

def test_the_rendered_profile_is_valid_yaml(tmp_path: Path, monkeypatch):
    import yaml

    _write(tmp_path, {"go.mod": "module x\n"})
    monkeypatch.setattr("shutil.which", lambda c: f"/usr/bin/{c}")
    parsed = yaml.safe_load(render(detect(tmp_path)))
    assert isinstance(parsed["checks"], dict)
    assert all(isinstance(v, list) for v in parsed["checks"].values())


def test_the_profile_explains_why_an_unbound_check_is_not_a_failure(tmp_path: Path):
    out = render(detect(tmp_path))
    assert "skip-pass" in out
    assert "VISE_NODE_GATE_OVERRIDE=1" in out, (
        "el archivo tiene que decir POR QUÉ no se liga lo ausente, o el próximo "
        "lector lo 'arregla' agregando herramientas que no están"
    )


def test_an_existing_profile_is_never_overwritten_silently(tmp_path: Path, capsys):
    import argparse

    from vise.cli.bootstrap_cmd import _cmd_bootstrap

    (tmp_path / ".vise").mkdir()
    (tmp_path / ".vise" / "quality.yaml").write_text("checks:\n  lint: [\"mio\"]\n")
    _write(tmp_path, {"pyproject.toml": ""})

    rc = _cmd_bootstrap(argparse.Namespace(
        project_dir=str(tmp_path), dry_run=False, force=False
    ))
    assert rc == 0
    assert "already exists" in capsys.readouterr().out
    assert "mio" in (tmp_path / ".vise" / "quality.yaml").read_text()


def test_force_overwrites(tmp_path: Path):
    import argparse

    from vise.cli.bootstrap_cmd import _cmd_bootstrap

    (tmp_path / ".vise").mkdir()
    (tmp_path / ".vise" / "quality.yaml").write_text("checks: {}\n")
    _write(tmp_path, {"go.mod": "module x\n"})

    _cmd_bootstrap(argparse.Namespace(
        project_dir=str(tmp_path), dry_run=False, force=True
    ))
    assert "generated by" in (tmp_path / ".vise" / "quality.yaml").read_text()


def test_dry_run_writes_nothing(tmp_path: Path):
    import argparse

    from vise.cli.bootstrap_cmd import _cmd_bootstrap

    _write(tmp_path, {"go.mod": "module x\n"})
    _cmd_bootstrap(argparse.Namespace(
        project_dir=str(tmp_path), dry_run=True, force=False
    ))
    assert not (tmp_path / ".vise" / "quality.yaml").exists()


def test_it_prints_the_env_vars_the_gates_need(tmp_path: Path, monkeypatch, capsys):
    """Sin VISE_TEST_CMD/VISE_LINT_CMD la puerta existe pero no muerde."""
    import argparse

    from vise.cli.bootstrap_cmd import _cmd_bootstrap

    _write(tmp_path, {"go.mod": "module x\n"})
    monkeypatch.setattr("shutil.which", lambda c: f"/usr/bin/{c}")
    _cmd_bootstrap(argparse.Namespace(
        project_dir=str(tmp_path), dry_run=False, force=True
    ))
    out = capsys.readouterr().out
    assert "VISE_TEST_CMD" in out
    assert "VISE_LINT_CMD" in out
    assert "unverified" in out


# ---------------------------------------------------------------------------
# cableado en el CLI
# ---------------------------------------------------------------------------

def test_the_subcommand_is_reachable_from_the_cli(tmp_path: Path, capsys):
    from vise.cli.main import main

    _write(tmp_path, {"go.mod": "module x\n"})
    rc = main(["bootstrap", "--project-dir", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "checks:" in capsys.readouterr().out


def test_the_usage_line_lists_it():
    """Un subcomando que el help no nombra es un subcomando que nadie encuentra."""
    from vise.cli.main import main

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["help"])
    assert "bootstrap" in buf.getvalue()


def test_secrets_binds_to_a_venv_detect_secrets(tmp_path: Path, monkeypatch) -> None:
    """detect-secrets installed only in the venv still binds `secrets`.

    It is a Python package, so on a repo that followed vise's own setup it
    lives in `.venv/bin` and never reaches PATH. Probing PATH alone reported
    "no detect-secrets" against a repo that had it working — and an unbound
    check reads to the user as a gap to accept knowingly, so under-detection
    talks them into accepting a hole that is not there.

    PATH is emptied and module resolution stubbed, so the only thing under
    test is that a venv-form candidate exists for `secrets` and is preferred.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(bootstrap_cmd, "_module_importable", lambda _i, _m: True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")

    found = detect(tmp_path)

    assert found["bound"]["secrets"] == [
        ".venv/bin/python", "-m", "detect_secrets", "scan",
    ]
