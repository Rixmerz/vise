"""What language servers the installed plugins declare, and who collides.

Claude Code's LSP manifest schema — read out of its own Zod definition — has
**no priority field, no workspace-root marker and no project-level override**:
`lspServers` is plugin-scoped and nothing arbitrates between plugins. So when
two installed plugins both claim `.ts`, which one wins is undetermined, and the
symptom is a language server that silently answers for the wrong toolchain.

vise itself declares none, and that is the same rule applied to itself: the
official marketplace ships one plugin per language covering exactly the set
vise used to duplicate, so shipping them here could only make resolution
undefined for anyone who installed both. This module is what replaced them —
it reads everyone else's declarations so `vise doctor` can report which servers
this session actually has, which binaries back them, and which extension is
claimed twice.

What it does NOT do is decide a collision. There is no correct answer to pick —
the schema provides no way to express one — so this reports the overlap and
names both claimants, which is what a person needs to go and remove one.

Two sources, because a plugin can declare its servers in either. Most put
`lspServers` in their own `.claude-plugin/plugin.json`. The official
per-language LSP plugins ship **no manifest at all** — their install directory
holds a LICENSE and a README, and the whole declaration lives in the
marketplace entry that installed them. Reading only the first source made this
module blind to every one of them: on a machine with vise's old twelve servers
and four official LSP plugins enabled, it reported "0 conflicts" while twelve
extensions were claimed twice.

Same contract as `neighbour_state`: reads someone else's files, never raises,
and distinguishes "nothing claims this twice" from "could not tell".
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Claude Code's plugin root. `CLAUDE_CONFIG_DIR` moves the whole config tree;
#: `install.sh` and the plugin cache both assume `~/.claude` otherwise.
_DEFAULT_CONFIG_DIR = "~/.claude"

_INSTALLED = "plugins/installed_plugins.json"
_MANIFEST = ".claude-plugin/plugin.json"
_MARKETPLACES = "plugins/marketplaces"
_MARKETPLACE_MANIFEST = ".claude-plugin/marketplace.json"

#: Accepted by the schema and then rejected at load with "not yet implemented",
#: which happens *before* the server is registered. A server declaring one can
#: never start, however well its binary is installed. `test_plugin_lsp_manifest`
#: pins the list; `vise doctor` reports such a server as BROKEN rather than
#: MISSING, because installing the binary would not help.
UNIMPLEMENTED_FIELDS = ("startupTimeout", "shutdownTimeout", "restartOnCrash")


@dataclass(frozen=True)
class Claim:
    """One plugin's LSP server claiming one extension."""

    plugin: str
    server: str

    def __str__(self) -> str:
        return f"{self.plugin} → {self.server}"


@dataclass(frozen=True)
class Server:
    """One declared language server, whoever declared it.

    Carries what `vise doctor` needs to report on it without re-reading any
    manifest: the binary to look for, how to invoke it, what it claims, and
    whether Claude Code will refuse it at load.
    """

    plugin: str
    name: str
    command: str
    args: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    unimplemented: tuple[str, ...] = ()

    @property
    def invocation(self) -> str:
        return " ".join([self.command, *self.args])


@dataclass(frozen=True)
class Conflict:
    """An extension claimed by more than one server."""

    extension: str
    claims: tuple[Claim, ...]

    @property
    def within_one_plugin(self) -> bool:
        """Both claimants are the same plugin — a hand-edited manifest.

        Worth separating: the fix is a local edit to one file, and it is the
        exact mistake the README's Deno instructions warn about (adding a
        server without removing the extensions from the one that had them).
        """
        return len({claim.plugin for claim in self.claims}) == 1


@dataclass(frozen=True)
class Survey:
    """What was found, and whether the question could be answered at all."""

    conflicts: tuple[Conflict, ...] = ()
    #: Every server any installed plugin declares, sorted by name.
    servers: tuple[Server, ...] = ()
    #: Plugins whose declaration was resolved, from either source.
    surveyed: tuple[str, ...] = ()
    #: Plugins whose declaration could not be read from either source. These
    #: are the ones a conflict could be hiding behind, so they are reported
    #: rather than dropped — that silence is what made this module claim a
    #: clean machine while four official LSP plugins went unread.
    unresolved: tuple[str, ...] = ()
    known: bool = False
    detail: str = "not checked"

    @property
    def declaring(self) -> tuple[str, ...]:
        """Plugins that declare at least one server."""
        return tuple(sorted({server.plugin for server in self.servers}))


def config_dir() -> Path:
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or _DEFAULT_CONFIG_DIR
    return Path(raw).expanduser()


def _normalise(extension: Any) -> str | None:
    """`.TS` and `ts` are the same claim. Anything else is not an extension."""
    if not isinstance(extension, str):
        return None
    text = extension.strip().lower()
    if not text:
        return None
    return text if text.startswith(".") else f".{text}"


def _servers_from(raw: Any, install_path: Path) -> dict[str, Any]:
    """Resolve the three shapes `lspServers` accepts into one mapping.

    The schema takes a record, a path to a `.lsp.json` file, or an array of
    either. Handling only the record — the shape most plugins use — would make
    this blind to exactly the third-party manifest it exists to inspect.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        path = (install_path / raw).resolve()
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - an unreadable side file is not fatal
            return {}
        return _servers_from(loaded, install_path)
    if isinstance(raw, list):
        merged: dict[str, Any] = {}
        for entry in raw:
            merged.update(_servers_from(entry, install_path))
        return merged
    return {}


def _install_paths(root: Path) -> dict[str, Path]:
    """`{plugin@marketplace: installPath}` from the installed-plugins index."""
    index = json.loads((root / _INSTALLED).read_text(encoding="utf-8"))
    out: dict[str, Path] = {}
    for name, entries in (index.get("plugins") or {}).items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            path = (entry or {}).get("installPath")
            if isinstance(path, str) and path:
                # Several scopes can install the same plugin; the manifest is
                # the same file, so the last one wins and the survey is
                # unaffected.
                out[str(name)] = Path(path)
    return out


def _from_manifest(install_path: Path) -> dict[str, Any] | None:
    """`lspServers` out of the plugin's own manifest, or None if unreadable."""
    try:
        manifest = json.loads(
            (install_path / _MANIFEST).read_text(encoding="utf-8")
        )
    except Exception:  # noqa: BLE001 - an uninstalled, moved or manifest-less plugin
        return None
    if not isinstance(manifest, dict):
        return None
    return _servers_from(manifest.get("lspServers"), install_path)


def _from_marketplace(plugin: str, base: Path, install_path: Path) -> dict[str, Any] | None:
    """`lspServers` out of the marketplace entry that installed the plugin.

    Where the official per-language LSP plugins keep theirs: their install
    directory has no manifest to read, so this is the only source there is.
    """
    name, _, marketplace = str(plugin).partition("@")
    if not marketplace:
        return None
    catalogue = base / _MARKETPLACES / marketplace / _MARKETPLACE_MANIFEST
    try:
        loaded = json.loads(catalogue.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a marketplace can be removed after install
        return None
    for entry in (loaded or {}).get("plugins") or []:
        if isinstance(entry, dict) and str(entry.get("name")) == name:
            return _servers_from(entry.get("lspServers"), install_path)
    return None


def _declared(plugin: str, install_path: Path, base: Path) -> dict[str, Any] | None:
    """Every server one plugin declares, from whichever source has them.

    The plugin's own manifest wins when it declares any — it is the more
    specific file. An empty or absent one falls through to the marketplace
    entry rather than being read as "this plugin declares nothing", which is
    the case the official LSP plugins are in.
    """
    own = _from_manifest(install_path)
    if own:
        return own
    catalogued = _from_marketplace(plugin, base, install_path)
    if catalogued is not None:
        return catalogued
    return own  # {} when the manifest parsed and declared none; None if unreadable


def _to_server(plugin: str, name: str, config: Any) -> Server | None:
    if not isinstance(config, dict):
        return None
    command = config.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    raw_args = config.get("args") or []
    args = tuple(str(a) for a in raw_args) if isinstance(raw_args, list) else ()
    extensions = tuple(sorted({
        ext for ext in (
            _normalise(e) for e in (config.get("extensionToLanguage") or {})
        ) if ext is not None
    }))
    return Server(
        plugin=plugin,
        name=str(name),
        command=command.strip(),
        args=args,
        extensions=extensions,
        unimplemented=tuple(f for f in UNIMPLEMENTED_FIELDS if f in config),
    )


def survey(root: Path | None = None) -> Survey:
    """Every server the installed plugins declare, and every contested extension."""
    try:
        base = Path(root) if root is not None else config_dir()
        index_file = base / _INSTALLED
        if not index_file.is_file():
            return Survey(
                known=True,
                detail=f"no {_INSTALLED} under {base} — no plugins installed here",
            )
        paths = _install_paths(base)
    except Exception as exc:  # noqa: BLE001 - a report must not raise
        return Survey(detail=f"could not read the plugin index: {type(exc).__name__}: {exc}")

    claimed: dict[str, list[Claim]] = {}
    servers: list[Server] = []
    surveyed: list[str] = []
    unresolved: list[str] = []
    for plugin, install_path in sorted(paths.items()):
        declared = _declared(plugin, install_path, base)
        if declared is None:
            unresolved.append(plugin)
            continue
        surveyed.append(plugin)
        for name, config in declared.items():
            server = _to_server(plugin, name, config)
            if server is None:
                continue
            servers.append(server)
            for extension in server.extensions:
                claim = Claim(plugin, server.name)
                if claim not in claimed.setdefault(extension, []):
                    claimed[extension].append(claim)

    conflicts = tuple(
        Conflict(extension, tuple(claims))
        for extension, claims in sorted(claimed.items())
        if len(claims) > 1
    )
    declaring = len({server.plugin for server in servers})
    detail = (
        f"{declaring} of {len(surveyed)} installed plugin(s) declare language "
        f"servers; {len(conflicts)} extension(s) claimed more than once"
    )
    if unresolved:
        # Never report a clean machine off a partial read: a conflict can be
        # hiding in exactly the manifest that would not open.
        detail += (
            f"; {len(unresolved)} plugin(s) could not be read "
            f"({', '.join(sorted(unresolved))})"
        )
    return Survey(
        conflicts=conflicts,
        servers=tuple(sorted(servers, key=lambda s: (s.name, s.plugin))),
        surveyed=tuple(surveyed),
        unresolved=tuple(sorted(unresolved)),
        known=True,
        detail=detail,
    )


__all__ = [
    "UNIMPLEMENTED_FIELDS",
    "Claim",
    "Conflict",
    "Server",
    "Survey",
    "config_dir",
    "survey",
]
