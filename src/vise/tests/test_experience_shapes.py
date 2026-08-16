"""A repeat count has to survive the merge, or nothing can threshold on it.

``agent-autoheal`` decides between "anecdote" and "pattern" on whether the same
failure shape appears twice for one agent. It had nowhere to put that count, so
it encoded incidents as ``;;``-separated segments inside ``description`` — and
``ExperienceMemoryStore.record`` merges descriptions by keeping the **longest**
string. Two consequences, both silent:

  1. A shorter follow-up incident is discarded while ``record()`` still reports
     success, so the caller believes it stored something it did not.
  2. Bumping a tally in prose is the worst case of all: ``x1`` -> ``x2`` is the
     same length, so the increment never wins the comparison. The count the
     skill's trigger condition reads could not be incremented through the store
     that holds it — the cold path was unreachable by construction.

``shapes: dict[str, int]`` is the fix: a first-class map merged additively, so
the one field callers threshold on is the one field that never loses a write.

``occurrences`` is a different number and stays what it was — records merged
onto the entry, regardless of shape. Both are asserted here so a future change
cannot quietly collapse them into one.
"""
from __future__ import annotations

from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore


def _fresh() -> ExperienceMemoryStore:
    """A store re-read from disk, the way each hook invocation sees it.

    The autouse conftest fixture redirects ``$XDG_DATA_HOME`` per test, so this
    resolves to a tmpdir and never touches a real project's memory.
    """
    store = ExperienceMemoryStore()
    store.load(scope="project", project_name="proj")
    return store


def _entry(description: str, shape: str | None = None, **kw) -> ExperienceEntry:
    return ExperienceEntry(
        type="gate_blocked",
        file_pattern="agents/*.md",
        domain="general",
        description=description,
        severity="high",
        shapes={shape: 1} if shape else {},
        **kw,
    )


def test_shapes_merge_additively():
    store = ExperienceMemoryStore()
    store.record(_entry("first", shape="agent:debugger/no-repro"))
    store.record(_entry("second", shape="agent:debugger/no-repro"))
    store.record(_entry("third", shape="agent:frontend/index-key"))

    assert len(store.entries) == 1, "same type+pattern+domain must merge to one entry"
    assert store.entries[0].shapes == {
        "agent:debugger/no-repro": 2,
        "agent:frontend/index-key": 1,
    }


def test_the_second_occurrence_survives_a_shorter_description():
    """The exact loss that made autoheal's cold path unreachable.

    Incident two arrives with a shorter description than incident one. Under the
    old encoding its content lived in that description and was dropped whole;
    the count now lives in `shapes` and survives regardless.
    """
    store = ExperienceMemoryStore()
    store.record(_entry("a long and detailed first incident report", shape="slug-x"))
    store.record(_entry("short", shape="slug-x"))

    entry = store.entries[0]
    assert entry.shapes["slug-x"] == 2, "the repeat that triggers the cold path was lost"
    # Description merge is unchanged — longest still wins — which is now
    # harmless precisely because it no longer carries the count.
    assert entry.description == "a long and detailed first incident report"


def test_same_length_description_still_counts_the_repeat():
    """`x1` -> `x2` measures the same. Under the old scheme the bump vanished."""
    store = ExperienceMemoryStore()
    store.record(_entry("shape-y x1", shape="shape-y"))
    store.record(_entry("shape-y x2", shape="shape-y"))

    assert store.entries[0].shapes["shape-y"] == 2


def test_occurrences_and_shapes_measure_different_things():
    """One counts records merged; the other counts repeats of one shape."""
    store = ExperienceMemoryStore()
    store.record(_entry("one", shape="slug-a"))
    store.record(_entry("two", shape="slug-b"))
    store.record(_entry("three"))  # no shape at all

    entry = store.entries[0]
    assert entry.occurrences == 3, "three records merged onto this entry"
    assert entry.shapes == {"slug-a": 1, "slug-b": 1}, "shapeless records add nothing"


def test_shapes_survive_a_round_trip_through_disk():
    """Each hook invocation re-reads the store, so an in-memory count is no count."""
    _fresh().record(_entry("first", shape="slug-z"))
    _fresh().record(_entry("second", shape="slug-z"))

    on_disk = _fresh()
    assert on_disk.entries[0].shapes == {"slug-z": 2}, (
        "the repeat did not survive the reload — a caller thresholding on two "
        "occurrences would never fire"
    )


def test_a_record_written_before_shapes_existed_still_loads():
    """Old entries on disk have no `shapes` key; they must not crash from_dict."""
    entry = ExperienceEntry.from_dict({
        "id": "abc123",
        "type": "gate_blocked",
        "file_pattern": "agents/*.md",
        "domain": "general",
        "description": "recorded before the field existed",
        "occurrences": 4,
    })
    assert entry.shapes == {}
    assert entry.occurrences == 4


def test_merging_onto_a_legacy_entry_starts_its_shape_map():
    store = ExperienceMemoryStore()
    store.entries.append(ExperienceEntry.from_dict({
        "id": "legacy",
        "type": "gate_blocked",
        "file_pattern": "agents/*.md",
        "domain": "general",
        "description": "no shapes key on disk",
    }))
    store.record(_entry("new incident", shape="slug-new"))

    assert len(store.entries) == 1
    assert store.entries[0].shapes == {"slug-new": 1}
