"""Two writers, one set of rules for what an entry is.

`ExperienceMemoryStore.record` writes what the tools, the node gate and the
runtime record. `hooks/experience_recorder` writes what a commit taught, as its
own interpreter, on every `git commit`. They write the same two files.

They disagreed about identity, and that one cost data. The hook kept two commits
on one glob apart under `(type, pattern, description)`; the store's cross-process
merge holds one entry per `(type, pattern, domain)` and drops the rest of what it
finds on disk. So the second commit's lesson survived exactly until anything
called `save()`.
"""
from __future__ import annotations

import json

import pytest

from vise.core import experience_rules as rules
from vise.engines.experience_memory import (
    ExperienceEntry,
    ExperienceMemoryStore,
    update_confidence,
)


def _commit(description: str, *, seen: str, pattern: str = "src/api/*.py") -> dict:
    """What the hook builds for one touched glob."""
    return {
        "type": "fix", "file_pattern": pattern, "keywords": ["api"], "domain": "api",
        "description": description, "resolution": "", "severity": "medium",
        "occurrences": 1, "first_seen": seen, "last_seen": seen,
        "scope": "project", "project_origin": "p", "commit_hash": "abc",
    }


def _store() -> ExperienceMemoryStore:
    """The conftest fixture redirects XDG, so this is already isolated."""
    store = ExperienceMemoryStore()
    store.load("global")
    return store


# --- identity -------------------------------------------------------------


def test_the_two_writers_agree_on_what_makes_an_entry_the_same():
    store = ExperienceMemoryStore()
    entry = ExperienceEntry(type="fix", file_pattern="src/api/*.py", domain="api",
                            description="anything at all")
    assert store._dedup_key(entry) == rules.dedup_key(entry.to_dict())


def test_a_hook_written_entry_survives_the_stores_next_save(tmp_path):
    """The regression this file exists for. `_merged_with_disk` keeps one entry
    per store key and drops the rest, so an entry the hook filed under a key the
    store does not use was deleted by the next unrelated `save()`."""
    store = _store()
    path = store._file_path
    path.parent.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    rules.upsert(entries, _commit("fix: the token refresh raced with logout", seen="2026-09-01"))
    rules.upsert(entries, _commit("fix: rate limiter counted retries twice", seen="2026-09-02"))
    path.write_text(json.dumps({"entries": entries}))

    # Anything else records and saves, which merges memory with disk.
    store.entries = []
    store.record(ExperienceEntry(type="smell_fixed", file_pattern="src/other/*.py",
                                 domain="util", description="unrelated"))

    on_disk = json.loads(path.read_text())["entries"]
    kept = [e for e in on_disk if e["file_pattern"] == "src/api/*.py"]
    assert len(kept) == 1, "the hook's glob lost an entry to the merge"
    assert kept[0]["occurrences"] == 2, (
        "both commits reached the entry; before this, the second was dropped "
        "and the count stayed at 1"
    )
    assert kept[0]["last_seen"] == "2026-09-02", "the newer commit's timestamp won"


def test_two_commits_on_one_glob_become_one_counted_entry():
    """Stated, not silent. The second subject is still not kept — description
    merging keeps the longer string — but the entry now says it was seen twice
    instead of reporting one sighting of whichever survived."""
    entries: list[dict] = []
    rules.upsert(entries, _commit("fix: short", seen="2026-09-01"))
    rules.upsert(entries, _commit("fix: a considerably longer subject", seen="2026-09-02"))

    assert len(entries) == 1
    assert entries[0]["occurrences"] == 2
    assert entries[0]["description"] == "fix: a considerably longer subject"


def test_a_different_glob_is_a_different_entry():
    entries: list[dict] = []
    rules.upsert(entries, _commit("fix: one", seen="2026-09-01"))
    rules.upsert(entries, _commit("fix: two", seen="2026-09-01", pattern="src/ui/*.tsx"))
    assert len(entries) == 2


# --- the confidence curve -------------------------------------------------


@pytest.mark.parametrize("occurrences", [1, 2, 3, 5, 12, 40])
def test_both_writers_reach_the_same_confidence_for_the_same_count(occurrences):
    """The hook added 0.1 per sighting, which reaches the cap in five and cannot
    tell five from fifty. The store's curve is asymptotic and never quite 1."""
    assert update_confidence(0.0, occurrences) == rules.confidence_for(occurrences)


def test_confidence_never_reaches_certainty():
    assert rules.confidence_for(1000) < 1.0
    assert rules.confidence_for(1000) == pytest.approx(rules.CONFIDENCE_CAP)


def test_confidence_rises_with_every_sighting():
    values = [rules.confidence_for(n) for n in range(1, 20)]
    assert values == sorted(values)
    assert len(set(values)) == len(values), "a curve that plateaus cannot rank"


# --- what a new entry carries ---------------------------------------------


def test_the_hook_now_assigns_an_id_and_a_first_seen():
    """An entry nobody can address cannot be bumped, gc'd or cited. The hook
    wrote neither field, so the store filled the id in later — from whichever
    process happened to merge it."""
    entries: list[dict] = []
    rules.upsert(entries, _commit("fix: something", seen="2026-09-01"))
    assert entries[0]["id"]
    assert entries[0]["first_seen"] == "2026-09-01"


def test_the_cap_is_applied_by_whoever_writes():
    """Skipping it did not add headroom; it left the trim to the store's own
    save, on entries the hook had already grown past it."""
    entries = [
        {"type": "fix", "file_pattern": f"src/a{i}/*.py", "domain": "api",
         "confidence": i / 1000.0, "last_seen": "2026-09-01"}
        for i in range(rules.MAX_ENTRIES + 50)
    ]
    trimmed = rules.evict(entries)
    assert len(trimmed) == rules.MAX_ENTRIES
    assert min(e["confidence"] for e in trimmed) > min(e["confidence"] for e in entries), \
        "the least confident should be the ones dropped"


def test_evict_leaves_a_store_under_the_cap_alone():
    entries = [{"type": "fix", "file_pattern": "a", "domain": "d", "confidence": 0.5}]
    assert rules.evict(entries) is entries


# --- the write itself -----------------------------------------------------


def test_the_hook_never_leaves_a_truncated_store(tmp_path, monkeypatch):
    """`write_text` opens mode 'w', which truncates before a byte of the new
    content lands — and this file has two documented readers in other processes.
    A failed write must leave the old store readable, not empty."""
    import vise.core.atomic
    import vise.hooks.experience_recorder as rec

    path = tmp_path / "experience_memory.json"
    path.write_text(json.dumps({"entries": [{"description": "the old content"}]}))

    def boom(*_a, **_kw):
        raise OSError("disk is a lie")

    # Patched at the source, because `_save_store` imports it at call time —
    # this hook defers everything past its commit gate so an `ls` does not pay
    # for what only a commit uses.
    monkeypatch.setattr(vise.core.atomic, "write_atomic", boom)
    rec._save_store(path, {"entries": [{"description": "the new content"}]})

    survived = json.loads(path.read_text())["entries"]
    assert survived == [{"description": "the old content"}]
