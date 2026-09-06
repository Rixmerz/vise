"""Two plugins claiming one extension, which nothing in Claude Code arbitrates.

`lspServers` is plugin-scoped and its schema has no priority field, no
workspace-root marker and no project-level override — so when two installed
plugins both claim `.ts`, which wins is undetermined and the symptom is a
language server quietly answering for the wrong toolchain.

vise already refuses to ship that collision with itself: the README's Deno
section is the argument, and it says the `typescript` entry must give up the
same five extensions when someone enables `deno`. These tests cover the half
that rule could not reach — a collision arriving from a plugin vise has never
heard of.

Every fixture has the shape of a real manifest, including the two shapes vise's
own never uses, because the third-party manifest this exists to inspect is
exactly where they turn up.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vise.core.plugin_conflicts import config_dir, survey


def _plugin(root: Path, name: str, servers) -> Path:
    """Write one plugin manifest and return its install path."""
    install = root / name
    (install / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (install / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "lspServers": servers}), encoding="utf-8"
    )
    return install


def _index(root: Path, plugins: dict[str, Path]) -> None:
    index = root / "plugins"
    index.mkdir(parents=True, exist_ok=True)
    (index / "installed_plugins.json").write_text(
        json.dumps({
            "version": 2,
            "plugins": {
                name: [{"scope": "user", "installPath": str(path)}]
                for name, path in plugins.items()
            },
        }),
        encoding="utf-8",
    )


def _server(command: str, *extensions: str) -> dict:
    return {
        "command": command,
        "extensionToLanguage": {ext: "whatever" for ext in extensions},
    }


# ---------------------------------------------------------------------------
# The thing it exists to find
# ---------------------------------------------------------------------------

def test_two_plugins_claiming_one_extension_is_reported(tmp_path: Path):
    a = _plugin(tmp_path, "alpha", {"ts-a": _server("a", ".ts")})
    b = _plugin(tmp_path, "beta", {"ts-b": _server("b", ".ts")})
    _index(tmp_path, {"alpha@one": a, "beta@two": b})

    found = survey(tmp_path)
    assert found.known
    assert [c.extension for c in found.conflicts] == [".ts"]
    assert {claim.plugin for claim in found.conflicts[0].claims} == {
        "alpha@one", "beta@two"
    }
    assert not found.conflicts[0].within_one_plugin


def test_a_plugin_colliding_with_itself_is_told_apart(tmp_path: Path):
    """The README's Deno instructions warn about exactly this: adding a server
    without removing the extensions from the one that had them. The fix is a
    local edit to one file, so it is worth saying which case this is."""
    a = _plugin(tmp_path, "alpha", {
        "typescript": _server("tsserver", ".ts"),
        "deno": _server("deno", ".ts"),
    })
    _index(tmp_path, {"alpha@one": a})

    conflict = survey(tmp_path).conflicts[0]
    assert conflict.within_one_plugin
    assert {claim.server for claim in conflict.claims} == {"typescript", "deno"}


def test_distinct_extensions_are_not_a_conflict(tmp_path: Path):
    a = _plugin(tmp_path, "alpha", {"py": _server("pyright", ".py")})
    b = _plugin(tmp_path, "beta", {"go": _server("gopls", ".go")})
    _index(tmp_path, {"alpha@one": a, "beta@two": b})

    found = survey(tmp_path)
    assert found.conflicts == ()
    assert set(found.surveyed) == {"alpha@one", "beta@two"}


# ---------------------------------------------------------------------------
# The shapes a third-party manifest may use and vise's never does
# ---------------------------------------------------------------------------

def test_extensions_are_compared_case_and_dot_insensitively(tmp_path: Path):
    """`.TSX`, `.tsx` and `tsx` are one claim. Comparing them raw would let a
    real collision through on a manifest that merely spells it differently."""
    a = _plugin(tmp_path, "alpha", {"one": _server("a", ".TSX")})
    b = _plugin(tmp_path, "beta", {"two": _server("b", "tsx")})
    _index(tmp_path, {"alpha@one": a, "beta@two": b})

    assert [c.extension for c in survey(tmp_path).conflicts] == [".tsx"]


def test_lsp_servers_given_as_an_array_is_read(tmp_path: Path):
    """The schema accepts a record, a path, or an array of either. Reading only
    the record — the shape vise happens to use — would be blind to exactly the
    third-party manifest this inspects."""
    a = _plugin(tmp_path, "alpha", [
        {"one": _server("a", ".rb")},
        {"two": _server("b", ".rb")},
    ])
    _index(tmp_path, {"alpha@one": a})

    conflict = survey(tmp_path).conflicts[0]
    assert conflict.extension == ".rb" and conflict.within_one_plugin


def test_lsp_servers_given_as_a_side_file_path_is_followed(tmp_path: Path):
    install = tmp_path / "alpha"
    (install / ".claude-plugin").mkdir(parents=True)
    (install / "servers.lsp.json").write_text(
        json.dumps({"one": _server("a", ".lua")}), encoding="utf-8"
    )
    (install / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "alpha", "lspServers": "./servers.lsp.json"}),
        encoding="utf-8",
    )
    b = _plugin(tmp_path, "beta", {"two": _server("b", ".lua")})
    _index(tmp_path, {"alpha@one": install, "beta@two": b})

    assert [c.extension for c in survey(tmp_path).conflicts] == [".lua"]


def test_a_side_file_that_cannot_be_read_is_skipped_not_fatal(tmp_path: Path):
    install = tmp_path / "alpha"
    (install / ".claude-plugin").mkdir(parents=True)
    (install / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "alpha", "lspServers": "./missing.json"}),
        encoding="utf-8",
    )
    b = _plugin(tmp_path, "beta", {"two": _server("b", ".lua")})
    _index(tmp_path, {"alpha@one": install, "beta@two": b})

    found = survey(tmp_path)
    assert found.known and found.conflicts == ()
    assert "alpha@one" in found.surveyed


@pytest.mark.parametrize("junk", [{"one": "not a dict"}, {"one": {}}, 42, None])
def test_a_manifest_that_declares_nothing_usable_is_silent(tmp_path: Path, junk):
    a = _plugin(tmp_path, "alpha", junk)
    _index(tmp_path, {"alpha@one": a})
    found = survey(tmp_path)
    assert found.known and found.conflicts == ()


# ---------------------------------------------------------------------------
# Absent vs unreadable — the same split every other reader here makes
# ---------------------------------------------------------------------------

def test_no_plugins_installed_is_a_known_answer(tmp_path: Path):
    found = survey(tmp_path)
    assert found.known and found.conflicts == ()
    assert "no plugins installed" in found.detail


def test_an_unreadable_index_is_unknown_rather_than_clean(tmp_path: Path):
    """Reporting "no conflicts" for an index nobody could parse would read as
    a clean bill of health on a question that was never asked."""
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text("{ not json")
    found = survey(tmp_path)
    assert not found.known
    assert "could not read" in found.detail


def test_a_plugin_whose_files_are_gone_is_skipped(tmp_path: Path):
    """An index entry outlives an uninstall. Raising there would make the whole
    survey useless because of one stale row."""
    a = _plugin(tmp_path, "alpha", {"one": _server("a", ".ts")})
    _index(tmp_path, {"alpha@one": a, "ghost@two": tmp_path / "does-not-exist"})

    found = survey(tmp_path)
    assert found.known and found.surveyed == ("alpha@one",)


def test_the_config_dir_honours_the_env_var(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert config_dir() == tmp_path
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert config_dir() == Path("~/.claude").expanduser()


# ---------------------------------------------------------------------------
# vise's own manifest, against the rule it states
# ---------------------------------------------------------------------------

def test_vise_does_not_collide_with_itself(tmp_path: Path):
    """The README says exactly one server must own an extension, and refuses to
    bundle `deno` for that reason. This holds the shipped manifest to it."""
    repo = Path(__file__).resolve().parents[3]
    _index(tmp_path, {"vise@vise-dev": repo})
    found = survey(tmp_path)
    assert found.surveyed == ("vise@vise-dev",)
    assert found.conflicts == (), [
        (c.extension, [str(x) for x in c.claims]) for c in found.conflicts
    ]
