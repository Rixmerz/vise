"""One atomic file write, for the files more than one process reads.

`Path.write_text` opens mode 'w', which truncates before a single byte of the
new content lands. Every file this module is used for has a documented second
reader in another process — the MCP server reads what the CLI wrote, a hook
reads what a tool wrote — so that window is not theoretical.

The worst case is not lost data. `graph_state.json` is read by the PreToolUse
enforcer on every tool call in every session and every `claude -p` worker, and
a parse failure there sends it down its fail-open path: a tool the active node
blocks gets approved, and the gate reports approval rather than reporting that
it could not evaluate. Making the file unreadable-in-flight impossible is what
removes that, rather than teaching each reader to cope.

The pattern was already correct in exactly one place — `ExperienceMemoryStore.
save` — and wrong in four others, each with its own near-miss version. It lives
here now so there is one of it.

Deliberately stdlib-only and import-light: hooks run as their own interpreters
on every tool call, and anything imported here is paid for on each of them.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path | str, data: bytes | str, *, encoding: str = "utf-8") -> Path:
    """Publish ``data`` at ``path`` in one step, or leave the old file alone.

    ``mkstemp`` in the *destination directory*, so the final ``replace`` is a
    same-filesystem rename and therefore atomic; a temp file in ``/tmp`` would
    make it a copy, which is not. A unique name rather than a fixed
    ``.json.tmp``, because two processes sharing one temp path write into each
    other's half-finished bytes and then publish the result — the rename being
    atomic does not save you when what it renames is already torn.

    ``fsync`` before the rename: without it the rename can reach the disk
    before the contents, and a machine that loses power between them publishes
    a file of zeros.

    Raises on failure and leaves the previous contents in place. A caller that
    would rather degrade than raise decides that for itself; a writer that
    swallows its own failure is how a run resumes into a state that never
    happened.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = data.encode(encoding) if isinstance(data, str) else data

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        try:
            written = os.write(fd, payload)
            if written != len(payload):
                raise OSError(f"partial write to {tmp}: {written}/{len(payload)} bytes")
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(target)
    return target
