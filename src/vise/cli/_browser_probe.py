"""Ask whether a browser is available without Playwright's exit noise.

``render_harness.browser_status()`` returns the right answer and then
Playwright's own teardown writes ``Task was destroyed but it is pending!``
followed by a ``TargetClosedError`` traceback to stderr — at interpreter exit,
after the function has already returned. Reading the Chromium path requires
starting Playwright's driver process, and stopping it that quickly is what
provokes the message.

Inside a validator that is cosmetic: the answer lands in evidence and nobody
reads the process's stderr. In a command a person runs, it reads as a crash in
the tool that just told them everything is fine — and `vise bootstrap` is
precisely where someone is deciding whether this repo is set up correctly.

Asking in a subprocess contains the noise where it belongs and changes nothing
about what the gates do.
"""
from __future__ import annotations

import subprocess
import sys

_PROBE = (
    "from vise.engines.render_harness import browser_status\n"
    "ok, why = browser_status()\n"
    "print('1' if ok else '0', why, sep='\\t')\n"
)

_TIMEOUT_S = 60


def browser_status_quiet() -> tuple[bool, str]:
    """``(available, reason)`` — the same contract, without the stderr noise."""
    try:
        done = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True, text=True, timeout=_TIMEOUT_S, check=False,
        )
        head = (done.stdout or "").strip().splitlines()
        if not head or "\t" not in head[0]:
            return False, "could not determine whether a browser is available"
        flag, reason = head[0].split("\t", 1)
        return flag == "1", reason
    except Exception as exc:  # noqa: BLE001 - a report must not raise
        return False, f"could not check: {type(exc).__name__}: {exc}"
