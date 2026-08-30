"""vise CLI entry point — minimal for now; subcommands land in later waves."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from vise import __version__

# ponytail: install hints are static text, not a second copy of server
# behavior — the server list itself always comes from plugin.json, never
# hardcoded here.
_INSTALL_HINTS: dict[str, str] = {
    "clangd": "apt install clangd / brew install llvm",
    "csharp-ls": "dotnet tool install -g csharp-ls",
    "deno": "https://deno.land (ships with `deno lsp`, no separate install)",
    "gopls": "go install golang.org/x/tools/gopls@latest",
    "intelephense": "npm install -g intelephense",
    "jdtls": "https://github.com/eclipse-jdtls/eclipse.jdt.ls",
    "kotlin-lsp": "https://github.com/Kotlin/kotlin-lsp",
    "lua": "https://github.com/LuaLS/lua-language-server",
    "pyright": "npm install -g pyright  (or: pip install pyright)",
    "ruby-lsp": "gem install ruby-lsp",
    "rust-analyzer": "rustup component add rust-analyzer",
    "sourcekit-lsp": "bundled with the Swift toolchain (Xcode / swift.org)",
    "typescript": "npm install -g typescript-language-server typescript",
}


def _plugin_root() -> Path | None:
    """Walk up from this file looking for .claude-plugin/plugin.json."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            return parent
    return None


def _load_manifest() -> dict[str, Any] | None:
    root = _plugin_root()
    if root is None:
        return None
    try:
        return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def _cmd_doctor() -> int:
    manifest = _load_manifest()
    lines: list[str] = []

    lines.append("=== LSP servers (declared in .claude-plugin/plugin.json) ===")
    if manifest is None:
        lines.append("  could not locate/parse plugin.json")
        declared = 0
        installed = 0
    else:
        servers: dict[str, Any] = manifest.get("lspServers", {})
        declared = len(servers)
        installed = 0
        for name, cfg in sorted(servers.items()):
            binary = cfg.get("command", name)
            found = shutil.which(binary)
            exts = " ".join(sorted(cfg.get("extensionToLanguage", {}).keys()))
            if found:
                installed += 1
                lines.append(f"  {name:<15} [OK]      {exts}")
            else:
                hint = _INSTALL_HINTS.get(name, f"install `{binary}` and put it on PATH")
                lines.append(f"  {name:<15} [MISSING] {exts}  — install: {hint}")
        lines.append(f"declared: {declared} / installed: {installed}")

        # A Deno workspace under typescript-language-server fails EVERY LSP
        # call — that server hard-requires a `typescript` package in
        # node_modules, which a Deno project never has. `deno` is deliberately
        # not declared by default: it would claim five extensions `typescript`
        # already claims, and the manifest schema has no priority field, so
        # shipping both makes .ts resolution undefined for every user.
        # Deterministic opt-in beats a nondeterministic default.
        cwd = Path.cwd()
        is_deno = any((cwd / n).exists() for n in ("deno.json", "deno.jsonc"))
        ts_owns_ts = ".ts" in servers.get("typescript", {}).get(
            "extensionToLanguage", {}
        )
        if is_deno and "deno" not in servers and ts_owns_ts:
            lines.extend([
                "",
                "  NOTE: this is a Deno workspace, but `.ts` maps to",
                "  typescript-language-server, which cannot start without",
                "  node_modules/typescript — every LSP call here will fail.",
                "  Fix: add the block below to lspServers in",
                "  .claude-plugin/plugin.json AND remove .ts/.tsx/.js/.jsx/.mts",
                "  from the `typescript` entry so exactly one server owns them,",
                "  then restart Claude Code (the map is read at session start).",
                '    "deno": {',
                '      "command": "deno", "args": ["lsp"],',
                '      "extensionToLanguage": {',
                '        ".ts": "typescript", ".tsx": "typescriptreact",',
                '        ".js": "javascript", ".jsx": "javascriptreact",',
                '        ".mts": "typescript"',
                "      }",
                "    }",
            ])

    lines.append("")
    lines.append("=== Python diagnostics (vise's own ruff/mypy shell-out) ===")
    # ponytail: reuse engines.lsp_diagnostics._find_checker (venv-aware) rather
    # than re-implementing the venv-walk here — that duplication is the
    # drift risk this command exists to prevent. It's a private symbol in a
    # directory this command doesn't own, so the import is best-effort and
    # falls back to plain shutil.which if the name ever moves.
    try:
        from vise.engines.lsp_diagnostics import _find_checker
    except Exception:
        _find_checker = None  # type: ignore[assignment]
    for tool in ("ruff", "mypy"):
        found = _find_checker(tool) if _find_checker else shutil.which(tool)
        lines.append(f"  {tool:<15} [{'OK' if found else 'MISSING'}]" + (f"  {found}" if found else ""))

    lines.append("")
    lines.append("=== XDG state migration ===")
    try:
        from vise.core import paths as _paths
        from vise.core.xdg_migrate import LEGACY_DATA_DIR

        target = _paths.data_dir()
        pending = LEGACY_DATA_DIR.exists() and LEGACY_DATA_DIR.resolve() != target.resolve()
        if pending:
            lines.append(f"  migration PENDING: {LEGACY_DATA_DIR} -> {target}")
            lines.append("  run: vise migrate-state")
        else:
            lines.append("  no legacy/XDG split detected")
    except Exception as exc:  # pragma: no cover - defensive, doctor must not crash
        lines.append(f"  could not check ({exc})")

    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("version", "--version", "-V"):
        print(f"vise {__version__}")
        return 0
    if args and args[0] in ("--help", "-h", "help") or not args:
        print("vise — phase-gated workflows, experience memory, git snapshots")
        print(f"version {__version__}")
        print("usage: vise [version|graph|experience|insights|runtime|bootstrap|shot|migrate-state|doctor|help]   (run the MCP server with `vise-mcp`)")
        return 0
    if args[0] == "doctor":
        return _cmd_doctor()
    if args[0] == "migrate-state":
        from vise.core.xdg_migrate import migrate

        summary = migrate()
        if summary["skipped"]:
            print(f"vise migrate-state: nothing to do ({summary['reason']})")
            return 0
        print(f"vise migrate-state: merged legacy data from {summary['legacy_dir']} into {summary['target_dir']}")
        print(f"  experience entries merged (global): {summary['experience_merged']}")
        if summary["project_experience_merged"]:
            for proj, n in summary["project_experience_merged"].items():
                print(f"  experience entries merged ({proj}): {n}")
        if summary["states_copied"]:
            print(f"  project states copied: {', '.join(summary['states_copied'])}")
        if summary["dirs_copied"]:
            print(f"  directories copied: {', '.join(summary['dirs_copied'])}")
        if summary["files_copied"]:
            print(f"  files copied: {', '.join(summary['files_copied'])}")
        if summary.get("error"):
            print(f"  warning: {summary['error']}", file=sys.stderr)
        print(f"  legacy tree left in place at {summary['legacy_dir']} (not deleted)")
        return 0
    if args[0] in ("graph", "experience", "insights", "runtime", "bootstrap", "shot"):
        import argparse

        from vise.cli import (
            bootstrap_cmd,
            experience_cmd,
            graph_cmd,
            insights_cmd,
            runtime_cmd,
            shot_cmd,
        )

        parser = argparse.ArgumentParser(prog="vise")
        sub = parser.add_subparsers(dest="command")
        graph_cmd.add_parser(sub)
        experience_cmd.add_parser(sub)
        insights_cmd.add_parser(sub)
        bootstrap_cmd.add_parser(sub)
        runtime_cmd.add_parser(sub)
        shot_cmd.add_parser(sub)
        ns = parser.parse_args(args)
        func = getattr(ns, "func", None)
        if func is None:
            parser.parse_args([args[0], "--help"])
            return 2
        return int(func(ns) or 0)
    print(f"vise: unknown command {args[0]!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
