"""vise CLI entry point — minimal for now; subcommands land in later waves."""
from __future__ import annotations

import sys

from vise import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("version", "--version", "-V"):
        print(f"vise {__version__}")
        return 0
    if args and args[0] in ("--help", "-h", "help") or not args:
        print("vise — phase-gated workflows, experience memory, git snapshots")
        print(f"version {__version__}")
        print("usage: vise [version|help]   (run the MCP server with `vise-mcp`)")
        return 0
    print(f"vise: unknown command {args[0]!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
