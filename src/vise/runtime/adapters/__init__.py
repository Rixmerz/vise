"""Workers that execute a brief against something real.

One adapter today: ``claude_code``. The ``Worker`` protocol is three lines
precisely so a second one — a shell script, a different CLI, a hosted API — is a
new file rather than a change to the scheduler.
"""
from __future__ import annotations

__all__ = ["ClaudeCodeWorker"]

from vise.runtime.adapters.claude_code import ClaudeCodeWorker
