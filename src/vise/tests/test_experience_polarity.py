"""A lesson and its negation are two lessons.

Prose similarity cannot tell them apart. Measured on vise's own consolidation
threshold: "always close the pool before returning the handler" against
"never close the pool before returning the handler" is 0.889 by ``difflib``,
and "use the pooled connection in the migration runner" against "do not use
the pooled connection in the migration runner" is 0.933 — both over the 0.85
that ``experience_gc.consolidate`` merges at. deltarag measured the same blind
spot on an embedder (0.95 cosine between a term and its negation) and put a
polarity guard in front of every merge. This is vise's.

The guard lives in the identity rather than beside one merge, because there
are three writers and a garbage collector and every one of them merges on
``dedup_key``. A guard on one of them is a guard the others do not have.
"""
from __future__ import annotations

from datetime import datetime

from vise.core import experience_rules as rules
from vise.engines.experience_gc import consolidate
from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore

ALWAYS = "always close the pool before returning the handler"
NEVER = "never close the pool before returning the handler"
USE = "use the pooled connection in the migration runner"
DO_NOT_USE = "do not use the pooled connection in the migration runner"


def _entry(description: str, **over) -> dict:
    base = {
        "id": description[:4],
        "type": "gate_blocked",
        "file_pattern": "src/db/*.py",
        "domain": "data",
        "description": description,
        "occurrences": 1,
        "confidence": 0.5,
        "last_seen": datetime.now().isoformat(),
    }
    base.update(over)
    return base


def test_the_measurement_that_motivates_the_guard():
    """If this ever drops under the threshold the guard is redundant, and if
    it were not pinned nobody would know which."""
    from vise.engines.experience_gc import CONSOLIDATE_SIMILARITY, _description_similarity

    assert _description_similarity(ALWAYS, NEVER) >= CONSOLIDATE_SIMILARITY
    assert _description_similarity(USE, DO_NOT_USE) >= CONSOLIDATE_SIMILARITY


def test_polarity_reads_do_versus_dont():
    assert rules.polarity(ALWAYS) == ""
    assert rules.polarity(NEVER) == "negated"
    assert rules.polarity(USE) == ""
    assert rules.polarity(DO_NOT_USE) == "negated"
    assert rules.polarity("Don't await inside the lock") == "negated"
    assert rules.polarity("") == ""


def test_polarity_is_part_of_the_identity():
    a = rules.dedup_key(_entry(ALWAYS))
    b = rules.dedup_key(_entry(NEVER))
    assert a[:3] == b[:3], "same kind, place and domain"
    assert a != b, "and still not the same experience"


def test_consolidate_keeps_a_lesson_apart_from_its_negation():
    kept, merged = consolidate([_entry(ALWAYS, id="a"), _entry(NEVER, id="b")])
    assert len(kept) == 2
    assert merged == {}


def test_consolidate_still_merges_a_genuine_near_duplicate():
    """The guard must not have widened into "never merge anything"."""
    kept, merged = consolidate([
        _entry(ALWAYS, id="a"),
        _entry(ALWAYS + " indeed", id="b"),
    ])
    assert len(kept) == 1
    assert len(merged) == 1 and set(merged) | set(merged.values()) == {"a", "b"}


def test_two_negated_lessons_that_are_near_duplicates_still_merge():
    kept, _ = consolidate([_entry(NEVER, id="a"), _entry(NEVER + " indeed", id="b")])
    assert len(kept) == 1


def test_the_store_records_a_negation_as_a_new_entry(tmp_path):
    """`record` merges on the key. Before polarity was in it, the second call
    here bumped the first entry's occurrences and kept the longer description —
    which, for `USE` versus `DO_NOT_USE`, is the negation."""
    store = ExperienceMemoryStore()
    store.load(scope="project", project_name="p", project_dir=str(tmp_path))
    first = store.record(ExperienceEntry(
        type="gate_blocked", file_pattern="src/db/*.py", domain="data",
        description=USE, scope="project",
    ))
    second = store.record(ExperienceEntry(
        type="gate_blocked", file_pattern="src/db/*.py", domain="data",
        description=DO_NOT_USE, scope="project",
    ))
    assert first.id != second.id
    assert {e.description for e in store.entries} == {USE, DO_NOT_USE}
    assert first.occurrences == 1


def test_the_hook_side_upsert_keeps_them_apart_too():
    entries = rules.upsert([], _entry(USE, id=""))
    entries = rules.upsert(entries, _entry(DO_NOT_USE, id=""))
    assert [e["description"] for e in entries] == [USE, DO_NOT_USE]
