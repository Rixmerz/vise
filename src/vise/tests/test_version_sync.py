"""vise declares its version in three places; they must not drift.

This exists because they did. A release bumped pyproject.toml and
.claude-plugin/plugin.json but not ``vise.__version__``, so the plugin
installed and loaded the new build correctly while ``vise_version``
kept reporting the previous one — the tool lying about which code is
running is worse than no tool, because it is the thing you check first
when a fix appears not to have landed.

The plugin manifest version is also load-bearing on its own: the Claude
Code updater compares the declared version, not the commit, so content
shipped without a bump never reaches an install.
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


def test_all_three_version_declarations_agree():
    assert vise.__version__ == _pyproject_version() == _plugin_manifest_version(), (
        f"version drift: vise.__version__={vise.__version__!r}, "
        f"pyproject={_pyproject_version()!r}, "
        f"plugin.json={_plugin_manifest_version()!r}"
    )


def test_version_is_pep440_parseable():
    # Guards against a typo'd bump landing in all three at once.
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[ab]|rc)?\d*", vise.__version__), vise.__version__
