"""vise declares its version in four places; they must not drift.

This exists because they did. A release bumped pyproject.toml and
.claude-plugin/plugin.json but not ``vise.__version__``, so the plugin
installed and loaded the new build correctly while ``vise_version``
kept reporting the previous one — the tool lying about which code is
running is worse than no tool, because it is the thing you check first
when a fix appears not to have landed.

The plugin manifest version is also load-bearing on its own: the Claude
Code updater compares the declared version, not the commit, so content
shipped without a bump never reaches an install.

The fourth declaration — the marketplace entry — was checked by nobody and
had drifted five alphas behind (a5 while everything else said a10). It is the
version a user sees before installing, so a stale one advertises a build that
is not what they get.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import vise

REPO = Path(__file__).resolve().parents[3]


def _pyproject_version() -> str:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _plugin_manifest_version() -> str:
    return json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"]


def _marketplace_versions() -> list[str]:
    entry = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    return [p["version"] for p in entry["plugins"] if p.get("name") == "vise"]


def test_all_four_version_declarations_agree():
    marketplace = _marketplace_versions()
    assert marketplace, "marketplace.json declares no vise plugin entry"
    declared = {
        "vise.__version__": vise.__version__,
        "pyproject": _pyproject_version(),
        "plugin.json": _plugin_manifest_version(),
        **{f"marketplace[{i}]": v for i, v in enumerate(marketplace)},
    }
    assert len(set(declared.values())) == 1, f"version drift: {declared}"


def test_version_is_pep440_parseable():
    # Guards against a typo'd bump landing in all three at once.
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[ab]|rc)?\d*", vise.__version__), vise.__version__


def test_every_shipped_command_is_named_in_the_changelog():
    """Un comando nuevo sin línea de CHANGELOG es un comando que nadie descubre.

    Y peor: fue así como `/bootstrap` se publicó sin llegar a ningún install.
    Su commit aterrizó DESPUÉS del release de a16, así que `main` declaraba una
    versión ya instalada y `claude plugin update` no traía nada — el updater
    compara la versión declarada, no el commit. El propio docstring de este
    archivo lo advierte, y aun así pasó.

    Escribir la línea de CHANGELOG obliga a mirar bajo qué versión sale, que es
    el momento exacto en que se nota que hace falta un bump.
    """
    # Anteriores a que este CHANGELOG existiera. No se documentan
    # retroactivamente: inventar una entrada para una versión que ya salió es
    # peor que la ausencia, porque convierte el archivo en ficción.
    PREEXISTING = {"debug", "feature", "status", "quality"}

    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    shipped = sorted(p.stem for p in (REPO / "commands").glob("*.md"))
    missing = [
        c for c in shipped
        if c not in PREEXISTING and f"/{c}" not in changelog
    ]
    assert not missing, (
        f"comandos sin mención en el CHANGELOG: {missing}. "
        "Agregá la línea — y al hacerlo, fijate si la versión declarada ya se "
        "publicó, porque contenido bajo un número ya anunciado no llega a "
        "ningún install."
    )
