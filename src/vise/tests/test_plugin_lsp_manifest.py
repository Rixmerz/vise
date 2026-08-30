"""The LSP block in plugin.json, checked against what Claude Code accepts.

vise declares twelve language servers and ships none of the binaries. That is
fine — each is dormant until someone opens a file of its type. What is not fine
is declaring one Claude Code will refuse: it throws before the server is
registered, so the server can never start however well the binary is installed,
and nothing in vise's own output said so.

Two of them shipped in exactly that state. `jdtls` and `kotlin-lsp` carried
`startupTimeout`, and the plugin loader answers that with

    LSP server 'jdtls': startupTimeout is not yet implemented.
    Remove this field from the configuration.

These tests are the same shape as the rest of `test_asset_honesty.py`: a fact
restated in a manifest drifts from the thing that reads it, so the suite pins
it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

#: Fields Claude Code's plugin schema accepts on an LSP server entry. The
#: schema is a *strict* object, so anything outside this set is a load error —
#: including the `strict: false` this repo's installer once claimed to set.
ALLOWED_FIELDS = frozenset({
    "command", "args", "extensionToLanguage", "transport", "env",
    "initializationOptions", "settings", "workspaceFolder",
    "startupTimeout", "shutdownTimeout", "restartOnCrash", "maxRestarts",
})

#: Accepted by the schema and then rejected at load with "not yet implemented".
#: Declaring one is strictly worse than declaring nothing: the server is gone
#: and the manifest looks richer for it.
UNIMPLEMENTED_FIELDS = frozenset({
    "startupTimeout", "shutdownTimeout", "restartOnCrash",
})


def _manifest() -> dict:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".claude-plugin" / "plugin.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    pytest.skip("not running from a plugin checkout")


@pytest.fixture(scope="module")
def servers() -> dict:
    return _manifest().get("lspServers", {})


def test_no_server_declares_a_field_claude_code_refuses(servers):
    """A declared server that cannot start is worse than an undeclared one."""
    offenders = {
        name: sorted(set(cfg) & UNIMPLEMENTED_FIELDS)
        for name, cfg in servers.items()
        if set(cfg) & UNIMPLEMENTED_FIELDS
    }
    assert not offenders, (
        f"these servers can never start — Claude Code throws "
        f"'<field> is not yet implemented' before registering them: {offenders}"
    )


def test_no_server_declares_a_field_outside_the_schema(servers):
    """The schema is a strict object; an unknown key fails the whole entry."""
    unknown = {
        name: sorted(set(cfg) - ALLOWED_FIELDS)
        for name, cfg in servers.items()
        if set(cfg) - ALLOWED_FIELDS
    }
    assert not unknown, f"fields Claude Code's schema does not accept: {unknown}"


def test_every_server_declares_a_command_and_at_least_one_extension(servers):
    for name, cfg in servers.items():
        assert cfg.get("command"), f"{name} declares no command"
        assert cfg.get("extensionToLanguage"), (
            f"{name} maps no extension, which the schema rejects outright"
        )


def test_a_command_carries_no_spaces(servers):
    """The schema refuses one: arguments belong in `args`."""
    for name, cfg in servers.items():
        command = cfg["command"]
        assert " " not in command or command.startswith("/"), (
            f"{name}: {command!r} — put arguments in `args`"
        )


def test_extension_keys_are_lowercase(servers):
    """Claude Code lowercases every key when it builds the extension map.

    So an uppercase key is not a distinct entry, it is a silent duplicate of
    its lowercase twin. clangd used to map `.C` and `.H` to cpp beside `.c` and
    `.h` mapped to c — the intent was unreachable and the collision invisible.
    """
    for name, cfg in servers.items():
        for ext in cfg["extensionToLanguage"]:
            assert ext == ext.lower(), (
                f"{name}: {ext!r} is lowercased before use, colliding with "
                f"{ext.lower()!r}"
            )


def test_extension_keys_start_with_a_dot(servers):
    for name, cfg in servers.items():
        for ext in cfg["extensionToLanguage"]:
            assert ext.startswith("."), f"{name}: {ext!r} should start with '.'"


def test_no_two_servers_claim_the_same_extension(servers):
    """The schema has no priority field, so two claimants make resolution
    undefined for every user — `vise doctor` says exactly this about Deno."""
    owners: dict[str, list[str]] = {}
    for name, cfg in servers.items():
        for ext in cfg["extensionToLanguage"]:
            owners.setdefault(ext.lower(), []).append(name)
    contested = {ext: names for ext, names in owners.items() if len(names) > 1}
    assert not contested, f"more than one server claims: {contested}"


def test_every_declared_server_has_an_install_hint():
    """A server nobody can install is a row in `doctor` that helps nobody."""
    from vise.cli.main import _INSTALL_HINTS

    servers = _manifest().get("lspServers", {})
    missing = sorted(set(servers) - set(_INSTALL_HINTS))
    assert not missing, f"declared with no install hint in `vise doctor`: {missing}"
