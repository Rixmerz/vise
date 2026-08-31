"""Every event the runtime emits must be one telemetry accepts.

`record_run_event` drops an unrecognised kind with a warning nobody reads, so an
event added to the scheduler and not to the allowlist is simply absent from
`vise insights` — and absent reads as "never happened", not as "not recorded".

This is the same shape as the asset-honesty tests: two places state the same
fact, so something has to hold them together.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from vise.engines.telemetry import _VALID_RUN_EVENTS

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"

#: `state.emit("<kind>"` — the only way the runtime raises a run event.
_EMIT = re.compile(r"""\.emit\(\s*\n?\s*["']([a-z_]+)["']""")


def _emitted() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(RUNTIME.rglob("*.py")):
        kinds = set(_EMIT.findall(path.read_text(encoding="utf-8")))
        if kinds:
            found[path.name] = kinds
    return found


def test_the_runtime_emits_at_all():
    """A regex that matches nothing would make this file pass forever."""
    assert _emitted(), "no emit() calls found — the pattern has drifted"


def test_every_emitted_event_is_one_telemetry_accepts():
    unknown = {
        name: sorted(kinds - _VALID_RUN_EVENTS)
        for name, kinds in _emitted().items()
        if kinds - _VALID_RUN_EVENTS
    }
    assert not unknown, (
        f"these events are emitted and silently dropped by telemetry: {unknown}. "
        f"Add them to _VALID_RUN_EVENTS, or the runs they describe look like "
        f"they never happened"
    )


@pytest.mark.parametrize("kind", ["drained", "drain_failed", "replanned"])
def test_the_events_this_release_added_are_recorded(kind):
    """Named explicitly: each was added with the code that emits it."""
    assert kind in _VALID_RUN_EVENTS
