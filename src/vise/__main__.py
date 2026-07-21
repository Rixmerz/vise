"""python -m vise entry point — starts the stdio MCP server."""
from __future__ import annotations

from vise.server import serve


def run() -> None:
    """Console-script entry point (`vise-mcp`)."""
    serve()


if __name__ == "__main__":
    run()
