"""vise declares no language servers, and the rules if it ever does again.

It used to declare twelve — and the official marketplace ships one plugin per
language covering exactly those twelve, under the same server names. Since
`lspServers` is plugin-scoped with no priority field, no workspace-root marker
and no project-level override, a user with vise and any official LSP plugin got
undetermined resolution on every extension both claimed. That is not a corner
case: on the machine this was found on, four official LSP plugins were enabled
and twelve extensions were claimed twice.

vise's own README already makes this argument for one server — it refuses to
bundle `deno` beside `typescript` because "a deterministic opt-in beats a
nondeterministic default". Declaring twelve was the same defect twelve times,
so they are gone: language servers are installed per language, by the plugin
whose only job is that language, and `vise doctor` reports what the session
ended up with by reading everyone's manifests.

The schema rules below are kept as guards rather than deleted. They cost
nothing against an empty map and they are what a future server would have to
satisfy — including the one that shipped broken twice: `jdtls` and
`kotlin-lsp` carried `startupTimeout`, and the plugin loader answers that with

    LSP server 'jdtls': startupTimeout is not yet implemented.
    Remove this field from the configuration.

Both official plugins still ship that field today, which is why `vise doctor`
reports such a server as BROKEN rather than MISSING — installing the binary
cannot help.
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


#: Server names the official per-language LSP plugins declare. `vise doctor`
#: reports whatever the installed plugins declare, so its hint table is keyed
#: to these rather than to anything vise owns. An unknown name still gets a
#: generic "put it on PATH", so a miss degrades the row rather than losing it.
OFFICIAL_SERVER_NAMES = frozenset({
    "clangd", "csharp-ls", "gopls", "intelephense", "jdtls", "kotlin-lsp",
    "lua", "pyright", "ruby-lsp", "rust-analyzer", "sourcekit-lsp",
    "typescript",
})


def test_vise_declares_no_language_servers(servers):
    """The whole point of the change. Re-adding one re-creates the collision
    for every user who also installed the official plugin for that language,
    and nothing in Claude Code arbitrates between them."""
    assert servers == {}, (
        "vise declares language servers again: "
        f"{sorted(servers)}. The official marketplace ships one plugin per "
        "language; `lspServers` has no priority field, so shipping both makes "
        "resolution undefined for anyone with either installed."
    )


def test_doctor_can_name_an_install_for_every_official_server():
    """A row in `doctor` that says MISSING and nothing else helps nobody."""
    from vise.cli.main import _INSTALL_HINTS

    missing = sorted(OFFICIAL_SERVER_NAMES - set(_INSTALL_HINTS))
    assert not missing, f"no install hint in `vise doctor` for: {missing}"
