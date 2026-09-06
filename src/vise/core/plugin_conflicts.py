"""Which installed plugins claim the same file extension for an LSP server.

vise declares twelve language servers in `.claude-plugin/plugin.json` as a map
of extension to binary. Claude Code's LSP manifest schema — read out of its own
Zod definition — has **no priority field, no workspace-root marker and no
project-level override**: `lspServers` is plugin-scoped and nothing arbitrates
between plugins. So when two installed plugins both claim `.ts`, which one wins
is undetermined, and the symptom is a language server that silently answers for
the wrong toolchain.

vise already refuses to ship that collision with itself — the README's Deno
section explains why `deno` is opt-in rather than bundled, and says the
`typescript` entry must give up the same five extensions when someone enables
it. This module is the other half of that rule: the same collision can arrive
from a plugin vise has never heard of, and nothing anywhere reports it.

What it does NOT do is decide. There is no correct answer to pick — the schema
provides no way to express one — so this reports the overlap and names both
claimants, which is what a person needs to go and remove one.

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


@dataclass(frozen=True)
class Claim:
    """One plugin's LSP server claiming one extension."""

    plugin: str
    server: str

    def __str__(self) -> str:
        return f"{self.plugin} → {self.server}"


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
    #: Plugins whose manifest was read successfully.
    surveyed: tuple[str, ...] = ()
    known: bool = False
    detail: str = "not checked"


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
    either. Handling only the record — the shape vise happens to use — would
    make this blind to exactly the third-party manifest it exists to inspect.
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


def survey(root: Path | None = None) -> Survey:
    """Every extension claimed by more than one installed LSP server."""
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
    surveyed: list[str] = []
    for plugin, install_path in sorted(paths.items()):
        try:
            manifest = json.loads(
                (install_path / _MANIFEST).read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001 - an uninstalled or moved plugin
            continue
        surveyed.append(plugin)
        for server, config in _servers_from(
            manifest.get("lspServers"), install_path
        ).items():
            if not isinstance(config, dict):
                continue
            for extension in (config.get("extensionToLanguage") or {}):
                key = _normalise(extension)
                if key is None:
                    continue
                claimed.setdefault(key, []).append(Claim(plugin, str(server)))

    conflicts = tuple(
        Conflict(extension, tuple(claims))
        for extension, claims in sorted(claimed.items())
        if len(claims) > 1
    )
    return Survey(
        conflicts=conflicts,
        surveyed=tuple(surveyed),
        known=True,
        detail=(
            f"{len(surveyed)} installed plugin(s) declare language servers; "
            f"{len(conflicts)} extension(s) claimed more than once"
        ),
    )


__all__ = ["Claim", "Conflict", "Survey", "config_dir", "survey"]
