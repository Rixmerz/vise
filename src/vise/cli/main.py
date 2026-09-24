"""vise CLI entry point — minimal for now; subcommands land in later waves."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from vise import __version__

# ponytail: install hints are static text, not a second copy of server
# behavior — the server list itself is read off the installed plugins, never
# hardcoded here. vise declares no servers of its own, so every name below
# belongs to someone else's manifest; an unknown one falls back to a generic
# "put it on PATH" rather than going unreported.
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


#: How long a healthy server has to prove it is one by staying alive.
_PROBE_SETTLE_S = 1.5


def _probe(binary: str, args: list[str]) -> tuple[bool, str]:
    """Whether a language server on PATH can actually be started.

    ``shutil.which`` answers "a file with that name exists on PATH", which is a
    different question. ``rustup`` installs a ``rust-analyzer`` shim that exits
    with *"error: Unknown binary 'rust-analyzer' in official toolchain"* until
    the component is added: present, found, and dead. Reporting that as ``[OK]``
    is the assert-instead-of-verify mistake vise's own gates exist to prevent,
    and it is how a declared server ends up unusable with the doctor saying it
    is fine.

    The probe is to start it **exactly as Claude Code will** — the declared
    command with the declared args — and see whether it is still alive a moment
    later. A language server speaking stdio blocks waiting for a request; one
    that cannot run exits at once, and its own output says why.

    ``--version`` was the obvious probe and is the wrong one:
    ``pyright-langserver`` does not implement it and exits complaining that no
    transport was selected, so a perfectly healthy server reports as suspect.
    A check that cries wolf on a working install teaches people to ignore it,
    which is the same failure as a gate that goes red for environment reasons.
    """
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv comes from the manifest
            [binary, *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"

    try:
        # `wait`, not `communicate`: communicate closes stdin, the server reads
        # EOF and exits cleanly, and the probe reports a healthy pyright as
        # dead. Still running after the settle window is the pass — it is
        # waiting for an LSP request, which is the whole job.
        proc.wait(timeout=_PROBE_SETTLE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        _close(proc)
        return True, "started and waited for input"

    # It exited on its own. The pipes cannot block now, and what it printed on
    # the way out is the only useful thing to show a person.
    out = proc.stdout.read() if proc.stdout else ""
    err = proc.stderr.read() if proc.stderr else ""
    _close(proc)
    detail = (err or out).strip().splitlines()
    return False, detail[0] if detail else f"exited {proc.returncode} with no output"


def _close(proc: subprocess.Popen) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:  # pragma: no cover - closing a dead pipe
                pass


def _cmd_doctor() -> int:
    lines: list[str] = []

    # One survey, two sections. It walks every installed plugin's declaration
    # — vise declares none of its own, deliberately: the official marketplace
    # ships one plugin per language covering exactly the set vise used to
    # duplicate, and `lspServers` has no priority field, so bundling them here
    # could only make resolution undefined for anyone who installed both.
    _survey_error = ""
    try:
        from vise.core.plugin_conflicts import survey as _lsp_survey

        lsp = _lsp_survey()
    except Exception as exc:  # pragma: no cover - defensive, doctor must not crash
        lsp = None
        _survey_error = f"could not check ({exc})"

    lines.append("=== LSP servers (declared by installed plugins) ===")
    if lsp is None:
        lines.append(f"  {_survey_error}")
    elif not lsp.known:
        lines.append(f"  {lsp.detail}")
    elif not lsp.servers:
        lines.append("  none — no installed plugin declares a language server.")
        lines.append(
            "  The `LSP` tool every code-touching agent carries has nothing to "
            "call until one does."
        )
        lines.append(
            "  Install the one for your language: `/plugin install "
            "pyright-lsp@claude-plugins-official` (also typescript-lsp,"
        )
        lines.append(
            "  gopls-lsp, clangd-lsp, rust-analyzer-lsp, ruby-lsp, php-lsp, "
            "swift-lsp, lua-lsp, csharp-lsp)."
        )
    else:
        verified = 0
        unusable: list[str] = []
        for server in lsp.servers:
            label = f"{server.name} ({server.plugin.split('@')[0]})"
            exts = " ".join(server.extensions)
            if server.unimplemented:
                # Claude Code throws on these before the server is registered,
                # so it can never start however well the binary is installed.
                unusable.append(server.name)
                lines.append(
                    f"  {label:<28} [BROKEN]  {exts}  — declares "
                    f"{', '.join(server.unimplemented)}, which Claude Code "
                    f"refuses (\"not yet implemented\"); the server is never "
                    f"registered"
                )
                continue
            if not shutil.which(server.command):
                hint = _INSTALL_HINTS.get(
                    server.name, f"install `{server.command}` and put it on PATH"
                )
                lines.append(f"  {label:<28} [MISSING] {exts}  — install: {hint}")
                continue
            ok, evidence = _probe(server.command, list(server.args))
            if ok:
                verified += 1
                lines.append(f"  {label:<28} [OK]      {exts}")
            else:
                unusable.append(server.name)
                lines.append(
                    f"  {label:<28} [ON PATH, UNVERIFIED]  {exts}\n"
                    f"{'':<32}`{server.invocation}` did not start: {evidence[:160]}"
                )
        lines.append(
            f"declared: {len(lsp.servers)} by {len(lsp.declaring)} plugin(s) "
            f"/ verified: {verified}"
        )
        if unusable:
            lines.append(
                f"  {len(unusable)} declared but not usable as configured: "
                f"{', '.join(sorted(set(unusable)))}"
            )

        # A Deno workspace under typescript-language-server fails EVERY LSP
        # call — that server hard-requires a `typescript` package in
        # node_modules, which a Deno project never has.
        cwd = Path.cwd()
        is_deno = any((cwd / n).exists() for n in ("deno.json", "deno.jsonc"))
        node_ts = [
            s for s in lsp.servers
            if ".ts" in s.extensions and "typescript-language-server" in s.command
        ]
        has_deno = any(s.command == "deno" for s in lsp.servers)
        if is_deno and node_ts and not has_deno:
            owner = node_ts[0].plugin
            lines.extend([
                "",
                "  NOTE: this is a Deno workspace, but `.ts` maps to",
                f"  typescript-language-server (from {owner}), which cannot",
                "  start without node_modules/typescript — every LSP call here",
                "  will fail. Disable that plugin for this machine and declare",
                "  `deno` instead, so exactly one server owns .ts/.tsx/.js/.jsx/.mts:",
                '    "deno": {',
                '      "command": "deno", "args": ["lsp"],',
                '      "extensionToLanguage": {',
                '        ".ts": "typescript", ".tsx": "typescriptreact",',
                '        ".js": "javascript", ".jsx": "javascriptreact",',
                '        ".mts": "typescript"',
                "      }",
                "    }",
                "  Then restart Claude Code — the map is read at session start.",
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
        checker = _find_checker(tool) if _find_checker else shutil.which(tool)
        lines.append(f"  {tool:<15} [{'OK' if checker else 'MISSING'}]" + (f"  {checker}" if checker else ""))

    lines.append("")
    lines.append("=== LSP extension conflicts (across installed plugins) ===")
    # Claude Code's LSP manifest schema has no priority field, no workspace-root
    # marker and no project-level override: `lspServers` is plugin-scoped and
    # nothing arbitrates. So two plugins claiming `.ts` is undetermined, and the
    # symptom is a server that quietly answers for the wrong toolchain. vise
    # already refuses to ship that collision with itself — the README's Deno
    # section is the whole argument — and this is the same rule applied to
    # plugins vise has never heard of.
    try:
        if lsp is None:
            raise RuntimeError(_survey_error)
        if not lsp.known:
            lines.append(f"  {lsp.detail}")
        elif not lsp.conflicts:
            lines.append(f"  none — {lsp.detail}")
        else:
            for conflict in lsp.conflicts:
                who = " vs ".join(str(claim) for claim in conflict.claims)
                lines.append(f"  {conflict.extension:<8} {who}")
                if conflict.within_one_plugin:
                    lines.append(
                        "           both are the same plugin — a manifest that "
                        "gained a server without giving up the extensions"
                    )
            lines.append(
                "  Nothing decides which of these wins; the schema cannot express "
                "a preference."
            )
            lines.append(
                "  Remove the extension from one manifest so exactly one server "
                "owns it."
            )
    except Exception as exc:  # pragma: no cover - defensive, doctor must not crash
        lines.append(f"  could not check ({exc})")

    lines.append("")
    lines.append("=== Render gates (ui_layout, ui_contrast) ===")
    # These fail CLOSED. `vise doctor` is where someone finds out what a repo
    # can and cannot check, and a gate that will refuse every run until a
    # browser exists belongs on that list next to the language servers — which
    # are the opposite case, dormant until needed.
    try:
        from vise.cli._browser_probe import browser_status_quiet

        ok, reason = browser_status_quiet()
        if ok:
            lines.append("  browser         [OK]  chromium is available")
        else:
            lines.append("  browser         [MISSING]")
            for hint in reason.splitlines():
                if hint.strip():
                    lines.append(f"                   {hint.strip()}")
        lines.append(
            "  a render gate also needs at least one `design.targets` entry in "
            ".vise/quality.yaml;"
        )
        lines.append(
            "  without one it fails closed rather than skipping. `vise bootstrap` says so per repo."
        )
    except Exception as exc:  # pragma: no cover - defensive, doctor must not crash
        lines.append(f"  could not check ({exc})")

    lines.append("")
    lines.append("=== Neighbouring MCP servers (vise names them, cannot call them) ===")
    # vise's skills and workflows teach these servers' tools. Nothing in this
    # repository can check that they are mounted — MCP has no server-to-server
    # channel — so what `doctor` reports is the minimum version the guidance
    # assumes plus, where the server leaves a file behind, whether this repo
    # has one. Reported here rather than re-derived in install.sh: the LSP hint
    # table was duplicated there once, and the copy went stale.
    try:
        from vise.core.neighbour_state import (
            graph_state,
            index_state,
            ledger_state,
            trace_state,
        )
        from vise.core.neighbours import MINIMUM_VERSIONS, TASKY_ABSENT_HINT

        project = Path.cwd()
        # tasky keeps one ledger per machine, but the line reads only this
        # repo's sessions out of it, so it is a per-repo footprint like the
        # other two.
        ledger = ledger_state(project)
        footprints = {
            "livespec": index_state(project).detail,
            "flowtrace": trace_state(project).detail,
        }
        if ledger.installed:
            footprints["tasky"] = ledger.detail
        for name, minimum in sorted(MINIMUM_VERSIONS.items()):
            found = footprints.get(name)
            lines.append(f"  {name:<17} needs >= {minimum}")
            if found:
                lines.append(f"                    this repo: {found}")
            if name == "tasky" and ledger.known and not ledger.installed:
                for hint in TASKY_ABSENT_HINT:
                    lines.append(f"                    {hint}")
        graph = graph_state(project)
        if graph.present:
            lines.append(f"  Graphify          {graph.detail}")
        lines.append(
            "  layout-inspector leaves no footprint in a repo — check your own tool surface."
        )
        lines.append("  `vise neighbours` reports this per project, with what follows from it.")
    except Exception as exc:  # pragma: no cover - defensive, doctor must not crash
        lines.append(f"  could not check ({exc})")

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
        print("usage: vise [version|graph|experience|insights|runtime|bootstrap|approve|shot|migrate-state|doctor|help]   (run the MCP server with `vise-mcp`)")
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
    if args[0] in (
        "graph", "experience", "insights", "runtime", "bootstrap", "approve",
        "shot", "neighbours",
    ):
        import argparse

        from vise.cli import (
            approve_cmd,
            bootstrap_cmd,
            experience_cmd,
            graph_cmd,
            insights_cmd,
            neighbours_cmd,
            runtime_cmd,
            shot_cmd,
        )

        parser = argparse.ArgumentParser(prog="vise")
        sub = parser.add_subparsers(dest="command")
        graph_cmd.add_parser(sub)
        experience_cmd.add_parser(sub)
        insights_cmd.add_parser(sub)
        bootstrap_cmd.add_parser(sub)
        approve_cmd.add_parser(sub)
        runtime_cmd.add_parser(sub)
        shot_cmd.add_parser(sub)
        neighbours_cmd.add_parser(sub)
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
