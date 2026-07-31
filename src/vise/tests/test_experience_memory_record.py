"""``ExperienceMemoryStore.record`` must persist merges, not just new entries.

``record()`` saved on the new-entry path and returned without saving on the
merge path, leaving persistence to the caller. Of its callers, only
``_record_node_gate_failure`` (``engines/node_gate.py``) actually recorded
without saving — a gate that blocked ten times stored ``occurrences: 1``, and
``update_confidence``, which is fed ``occurrences``, stayed frozen at its
first-occurrence value forever. (``hooks/workflow_post_traverse.py`` is not
part of this: its `_record_experience` always saved unconditionally, but the
function is unreachable — it imports a `workflow_manager` module that doesn't
exist in this repo, so it always falls through to a fallback path that never
touches ``ExperienceMemoryStore``.)

That silently disabled every "seen twice, so it is a pattern" reading built on
node-gate-recorded data, including ``experience_derive_checklist``'s
two-occurrence threshold.

The fix: the store now saves on both branches of ``record()``, so the store
owns its own persistence and no caller has to remember to call ``save()``.

These tests drive the store the way the non-saving caller does: ``record()``
with no explicit ``save()``, then a fresh load from disk.
"""
from __future__ import annotations

import os

import pytest

from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore


def _entry(description: str) -> ExperienceEntry:
    """An entry whose dedup key (type + file_pattern + domain) is constant."""
    return ExperienceEntry(
        type="smell_introduced",
        file_pattern="node:implement",
        keywords=["node-gate-failure", "implement"],
        description=description,
        severity="high",
        project_origin="proj",
        scope="project",
    )


def _fresh() -> ExperienceMemoryStore:
    store = ExperienceMemoryStore()
    store.load(scope="project", project_name="proj")
    return store


def test_repeated_record_persists_the_occurrence_count() -> None:
    """The counter must survive a reload — this is the whole bug."""
    for description in ("gate failed once", "gate failed twice", "gate failed thrice"):
        _fresh().record(_entry(description))  # no explicit save(), like node_gate.py

    on_disk = _fresh()
    assert len(on_disk.entries) == 1, "same dedup key must not create duplicates"
    assert on_disk.entries[0].occurrences == 3, (
        "every repeat was lost: record() returned from the merge branch without "
        "saving, so the three recordings collapsed to the first one"
    )


def test_confidence_grows_with_persisted_occurrences() -> None:
    """`confidence` is derived from `occurrences`, so a frozen count freezes it."""
    _fresh().record(_entry("first"))
    after_one = _fresh().entries[0].confidence

    for _ in range(4):
        _fresh().record(_entry("again"))
    after_five = _fresh().entries[0].confidence

    assert after_five > after_one, (
        f"confidence stayed at {after_one} across five recordings — "
        "update_confidence is fed a count that never reached disk"
    )


def test_a_brand_new_key_still_creates_a_separate_entry() -> None:
    """The fix must not make everything merge into one row."""
    _fresh().record(_entry("gate failure"))

    other = _entry("a different node entirely")
    other.file_pattern = "node:validate"
    _fresh().record(other)

    assert len(_fresh().entries) == 2, "distinct dedup keys must stay distinct"


def test_save_survives_a_failed_write_mid_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    """save() must not truncate the store in place.

    Simulate a crash after the temp file is created but before the atomic
    rename lands, by making the payload write raise. The property atomicity
    buys is that the *previous* on-disk contents are untouched — assert
    exactly that, not any particular implementation detail.
    """
    store = _fresh()
    store.record(_entry("first, safely persisted"))
    good_contents = store._file_path.read_text(encoding="utf-8")
    assert "first, safely persisted" in good_contents

    def _boom(fd, data):
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", _boom)

    with pytest.raises(OSError):
        store.record(_entry("second, must NOT reach disk"))

    survived = store._file_path.read_text(encoding="utf-8")
    assert survived == good_contents, (
        "a failed write corrupted the store instead of leaving the previous "
        "contents intact — save() is not atomic"
    )
    # No leaked temp file in the store's directory.
    leftovers = [p for p in store._file_path.parent.iterdir() if p.suffix == ".tmp"]
    assert not leftovers, f"failed write left temp file(s) behind: {leftovers}"
