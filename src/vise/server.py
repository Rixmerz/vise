"""FastMCP server — wires engines + tools into a single stdio MCP endpoint."""
from __future__ import annotations

import logging
import sys

from fastmcp import FastMCP

from vise import __version__

log = logging.getLogger(__name__)

mcp: FastMCP = FastMCP(
    name="vise",
    version=__version__,
    instructions=(
        "vise enforces phase-gated workflows, records cross-project experience "
        "memory, and keeps automatic git snapshots of your edits."
    ),
)


@mcp.tool()
def vise_version() -> dict[str, str]:
    """Return the installed vise version."""
    return {"version": __version__}


def serve() -> None:
    """Start the MCP server on stdio. Blocks until the client disconnects."""
    from vise.core.lifecycle import install_parent_death_signal
    from vise.tools.bootstrap import register_all

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    install_parent_death_signal()
    print(f"[vise] starting MCP server v{__version__}", file=sys.stderr)
    register_all(mcp)
    mcp.run()


if __name__ == "__main__":
    serve()
