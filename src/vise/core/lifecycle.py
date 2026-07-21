"""Process lifecycle installers for the vise MCP server.

``install_parent_death_signal`` — on Linux, uses PR_SET_PDEATHSIG so the
kernel SIGTERMs vise when its parent (claude) dies unexpectedly.

(jig's ``install_proxy_cleanup`` was removed — vise has no proxied MCP
subprocesses to reap.)
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def install_parent_death_signal() -> None:
    """On Linux, ask the kernel to SIGTERM us if the parent (claude) dies.

    Why: when the Claude Code session exits without cleanly closing stdio,
    the FastMCP loop can stay alive holding fastembed model weights.
    PR_SET_PDEATHSIG is a kernel-level guarantee — no polling needed.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes
        import signal as _signal

        PR_SET_PDEATHSIG = 1
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(PR_SET_PDEATHSIG, _signal.SIGTERM, 0, 0, 0)
    except Exception as e:
        log.debug("[vise.server] PR_SET_PDEATHSIG not installed: %s", e)
