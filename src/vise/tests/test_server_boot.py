"""The entry point every session starts through, executed rather than imported.

`serve()`, `vise.__main__.run()` and `install_parent_death_signal()` were never
called by any test and CI had no step that started the server, so the whole boot
path measured 0%. It is not a small path: it installs the death signal that stops
an orphaned server from holding fastembed's model weights, it registers the tool
surface, and — the part with no second chance — it must put nothing on stdout,
because stdout *is* the JSON-RPC stream. A stray print there does not degrade the
session; it corrupts the protocol on the first byte.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from vise import server


def test_serve_registers_the_tool_surface_and_runs_once(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append("run"))

    server.serve()

    assert calls == ["run"], "serve must hand control to the MCP loop exactly once"
    assert capsys.readouterr().out == "", (
        "nothing may reach stdout before the JSON-RPC stream — stdout is the "
        "protocol"
    )


def test_serve_puts_its_banner_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)

    server.serve()

    assert "starting MCP server" in capsys.readouterr().err


def test_serve_registers_more_than_the_module_level_tool(monkeypatch):
    """Before `register_all`, `mcp` carries only `vise_version`."""
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)

    server.serve()

    import asyncio

    names = {getattr(t, "name", str(t))
             for t in asyncio.run(server.mcp._list_tools())}
    assert "vise_version" in names
    assert len(names) > 40, f"the registry did not attach: {sorted(names)[:5]}"


def test_the_console_script_entry_point_calls_serve(monkeypatch):
    import vise.__main__ as entry

    called = []
    monkeypatch.setattr(entry, "serve", lambda: called.append(True))
    entry.run()

    assert called == [True]


def test_the_parent_death_signal_never_raises():
    """Best-effort by contract: a kernel that refuses prctl is not a crash."""
    from vise.core.lifecycle import install_parent_death_signal

    install_parent_death_signal()


def test_the_parent_death_signal_survives_a_missing_libc(monkeypatch):
    import ctypes

    from vise.core import lifecycle

    monkeypatch.setattr(
        ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(OSError("no libc"))
    )
    lifecycle.install_parent_death_signal()


@pytest.mark.parametrize("module", ["vise.server", "vise.__main__"])
def test_importing_the_entry_module_writes_nothing_to_stdout(module):
    """Import time counts too — the protocol starts before serve() does."""
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == "", f"{module} printed to stdout on import: {done.stdout!r}"
