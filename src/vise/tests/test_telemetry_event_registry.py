"""Every run event the scheduler emits must be one telemetry will record.

This defect has landed three times in one release. `drained` and
`drain_failed` were added to the scheduler and dropped by the cross-run log;
so was `resumed`, days later, by the person who had just fixed the other two.
Each time the symptom is the same and is almost invisible: the run's own
`state.json` has the event, `vise runtime explain` shows it, and the
cross-run log — the file that answers "which roles escalate most", "what does
a task of this shape usually cost" — silently does not, behind a
`log.warning` nobody reads.

Nothing here is fixed by this test; the two sets agree today. That is the
point. It is the fourth time this is meant to stop, and it costs one line in
`_VALID_RUN_EVENTS` to keep passing.

Both directions are asserted. An event emitted but unregistered is data
thrown away. An event registered but never emitted is a line that outlived
its reason, and the next reader cannot tell which of the two it is.
"""
from __future__ import annotations

import re
from pathlib import Path

from vise.engines.telemetry import _VALID_RUN_EVENTS

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"

#: `state.emit("kind", ...)` / `self.state.emit('kind')` — the literal first
#: argument. A computed kind would not be matched, and would be a bad idea for
#: exactly the reason this test exists: nothing could check it.
_EMIT = re.compile(r"\.emit\(\s*[\"']([a-z_]+)[\"']")


def _emitted() -> dict[str, list[str]]:
    """{event kind: [files that emit it]}"""
    found: dict[str, list[str]] = {}
    for path in sorted(RUNTIME.rglob("*.py")):
        for kind in _EMIT.findall(path.read_text(encoding="utf-8")):
            found.setdefault(kind, []).append(path.name)
    return found


def test_the_runtime_emits_events_worth_finding():
    """Guard against both assertions below passing because the scan found
    nothing — a doc-sync test that cannot see its subject reports success."""
    emitted = _emitted()
    assert len(emitted) >= 20, emitted
    assert "dispatched" in emitted and "run_started" in emitted


def test_every_emitted_event_is_registered_with_telemetry():
    emitted = _emitted()
    unregistered = {k: v for k, v in emitted.items() if k not in _VALID_RUN_EVENTS}
    assert not unregistered, (
        "these run events are emitted and silently dropped by the cross-run "
        f"log — add them to _VALID_RUN_EVENTS: {unregistered}"
    )


def test_no_registered_event_is_dead():
    emitted = set(_emitted())
    dead = sorted(_VALID_RUN_EVENTS - emitted)
    assert not dead, (
        "registered in telemetry but emitted by nothing — a line that outlived "
        f"its reason: {dead}"
    )


def test_the_two_sets_are_the_same_set():
    """Said once, plainly, so a failure names the whole difference rather than
    whichever half tripped first."""
    assert set(_emitted()) == set(_VALID_RUN_EVENTS)
