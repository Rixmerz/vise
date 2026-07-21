"""Tests for workflow_override_detector.py — user_override telemetry."""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest

HOOK_MOD = "vise.hooks.workflow_override_detector"


@pytest.fixture()
def tel_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "telemetry"
    d.mkdir()
    monkeypatch.setenv("VISE_TELEMETRY_DIR", str(d))
    return d


def _write_log(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def _run_hook(monkeypatch: pytest.MonkeyPatch, payload: dict, capsys) -> str:
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    if HOOK_MOD in sys.modules:
        del sys.modules[HOOK_MOD]
    mod = importlib.import_module(HOOK_MOD)
    mod.main()
    return capsys.readouterr().out


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _event(kind: str, prompt_hash: str, ts: datetime, **extra) -> dict:
    return {"ts": ts.isoformat(), "kind": kind, "prompt_hash": prompt_hash, "extra": extra}


# ---------------------------------------------------------------------------
# Detection cases
# ---------------------------------------------------------------------------

def test_reset_after_recent_hit_emits_override(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    pytest.importorskip("vise.engines.telemetry", reason="telemetry engine not yet extracted into vise")
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("auto_activate_hit", "h1", _now() - timedelta(minutes=5), workflow="debug")])

    out = _run_hook(monkeypatch, {"tool_name": "mcp__vise__graph_reset", "tool_input": {}}, capsys)
    assert "approve" in out

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[-1])
    assert rec["kind"] == "user_override"
    assert rec["prompt_hash"] == "h1"
    assert rec["extra"]["reason"] == "reset"
    assert rec["extra"]["from"] == "debug"


def test_activate_different_workflow_emits_switched(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    pytest.importorskip("vise.engines.telemetry", reason="telemetry engine not yet extracted into vise")
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("auto_activate_hit", "h2", _now() - timedelta(minutes=2), workflow="debug")])

    _run_hook(
        monkeypatch,
        {"tool_name": "mcp__vise__graph_activate", "tool_input": {"name": "feature-dev"}},
        capsys,
    )

    rec = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["kind"] == "user_override"
    assert rec["extra"]["reason"] == "switched"
    assert rec["extra"]["from"] == "debug"
    assert rec["extra"]["to"] == "feature-dev"


def test_activate_same_workflow_does_not_emit(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("auto_activate_hit", "h3", _now() - timedelta(minutes=1), workflow="debug")])

    _run_hook(
        monkeypatch,
        {"tool_name": "mcp__vise__graph_activate", "tool_input": {"name": "debug"}},
        capsys,
    )

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # no new event


def test_old_hit_outside_window_does_not_emit(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("auto_activate_hit", "h4", _now() - timedelta(hours=2), workflow="debug")])

    _run_hook(monkeypatch, {"tool_name": "mcp__vise__graph_reset", "tool_input": {}}, capsys)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_no_telemetry_log_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("VISE_TELEMETRY_DIR", str(tmp_path / "missing"))
    out = _run_hook(monkeypatch, {"tool_name": "mcp__vise__graph_reset", "tool_input": {}}, capsys)
    assert "approve" in out


def test_unrelated_tool_ignored(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("auto_activate_hit", "h5", _now() - timedelta(minutes=1), workflow="debug")])

    _run_hook(monkeypatch, {"tool_name": "Edit", "tool_input": {}}, capsys)

    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_double_override_suppressed(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Once user_override is recorded for a hit, a second reset shouldn't double-fire."""
    log = tel_dir / "orchestration.jsonl"
    _write_log(
        log,
        [
            _event("auto_activate_hit", "h6", _now() - timedelta(minutes=3), workflow="debug"),
            _event("user_override", "h6", _now() - timedelta(minutes=2), reason="reset", **{"from": "debug"}),
        ],
    )

    _run_hook(monkeypatch, {"tool_name": "mcp__vise__graph_reset", "tool_input": {}}, capsys)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # no new override


def test_no_hit_in_log(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("pre_plan_emit", "h7", _now(), variant="bug")])

    _run_hook(monkeypatch, {"tool_name": "mcp__vise__graph_reset", "tool_input": {}}, capsys)

    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_malformed_payload_safe(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", StringIO("not-json"))
    if HOOK_MOD in sys.modules:
        del sys.modules[HOOK_MOD]
    mod = importlib.import_module(HOOK_MOD)
    mod.main()
    out = capsys.readouterr().out
    assert "approve" in out


def test_activate_without_name_arg_no_emit(
    tel_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    log = tel_dir / "orchestration.jsonl"
    _write_log(log, [_event("auto_activate_hit", "h8", _now() - timedelta(minutes=1), workflow="debug")])

    _run_hook(monkeypatch, {"tool_name": "mcp__vise__graph_activate", "tool_input": {}}, capsys)

    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
