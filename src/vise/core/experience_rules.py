"""The rules for writing an experience entry. Two writers, one set of them.

`ExperienceMemoryStore.record` writes what the MCP tools, the node gate and the
runtime record. `hooks/experience_recorder` writes what a commit taught, as its
own interpreter, on every `git commit`. They write the same two files and they
disagreed about what an entry *is*:

=====================  ==============================  =========================
                       store                           commit hook
=====================  ==============================  =========================
identity               (type, file_pattern, domain)    (type, pattern, description)
confidence on repeat   0.95 * (1 - 0.7**n)             min(0.95, conf + 0.1)
confidence when new    0.285                           0.5, fixed
id                     uuid4[:8]                       none
first_seen             set                             never set
scope                  set                             never set
cap                    500, lowest confidence evicted  none
write                  temp file, fsync, rename        `write_text`
=====================  ==============================  =========================

The identity row is the one with teeth, and not because the store grows. The
store's own cross-process merge — `_merged_with_disk`, which is how two writers
are meant to coexist — holds one entry per *store* key and drops the rest of
what it finds on disk. So two commits touching one glob, which the hook kept
apart under its own key, became one entry the next time anything called
`save()`, and the second commit's lesson was gone. Measured, not reasoned:
`test_experience_writer_rules.py` records the two and asserts both survive.

Everything here takes plain dicts and returns them, because the store holds
dataclasses and the hook holds JSON, and a rule that only one representation
can express is a rule the other will reimplement. Standard library only, for
the usual reason: this is imported by something that runs on every commit.
"""
from __future__ import annotations

import uuid
from typing import Any, Mapping

#: What makes two entries the same experience. The store's merge already used
#: this and only this, so it was never really a choice the hook got to make.
DEDUP_FIELDS: tuple[str, ...] = ("type", "file_pattern", "domain")

#: How many entries a store keeps. Applied to the union at save time, so a
#: writer that skipped it was not adding headroom, it was leaving the cap to
#: whichever process saved next.
MAX_ENTRIES = 500

#: Asymptotic confidence: 0.29 → 0.50 → 0.65 → 0.76 → ... capped short of 1.0,
#: because an experience seen ten times is still not a law. A linear +0.1 per
#: sighting reaches the cap in five and cannot distinguish five from fifty.
CONFIDENCE_CAP = 0.95
CONFIDENCE_DECAY = 0.7


def dedup_key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    """The identity of an experience: same kind, same place, same domain."""
    return tuple(str(entry.get(f) or "") for f in DEDUP_FIELDS)  # type: ignore[return-value]


def confidence_for(occurrences: int) -> float:
    """Confidence after *occurrences* sightings."""
    return min(CONFIDENCE_CAP, CONFIDENCE_CAP * (1 - CONFIDENCE_DECAY ** max(1, occurrences)))


def new_id() -> str:
    """A short id. An entry nobody can address cannot be bumped, gc'd or cited."""
    return str(uuid.uuid4())[:8]


def merge(existing: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Fold *incoming* into *existing* in place, and return it.

    `shapes` is the one field that merges additively, and it is the reason this
    is a function rather than a convention: the counts are what a caller
    thresholds on, and prose merged by keeping the longer string discards a
    shorter follow-up silently — an `x1` bumped to `x2` is the same length.
    """
    existing["occurrences"] = int(existing.get("occurrences") or 1) + 1
    existing["confidence"] = confidence_for(existing["occurrences"])
    if incoming.get("last_seen"):
        existing["last_seen"] = incoming["last_seen"]
    if not existing.get("first_seen"):
        existing["first_seen"] = incoming.get("first_seen") or existing.get("last_seen") or ""
    if not existing.get("id"):
        existing["id"] = new_id()

    shapes = dict(existing.get("shapes") or {})
    for shape, count in (incoming.get("shapes") or {}).items():
        shapes[shape] = shapes.get(shape, 0) + count
    existing["shapes"] = shapes

    # Longer wins, which is arbitrary and stays that way: `shapes` carries what
    # is counted, so description is prose again rather than a datastore.
    if len(str(incoming.get("description") or "")) > len(str(existing.get("description") or "")):
        existing["description"] = incoming["description"]
    if incoming.get("resolution") and not existing.get("resolution"):
        existing["resolution"] = incoming["resolution"]

    related = list(existing.get("related_files") or [])
    for path in incoming.get("related_files") or []:
        if path not in related:
            related.append(path)
    existing["related_files"] = related
    return existing


def evict(entries: list[dict[str, Any]], cap: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    """Trim to *cap*, dropping the least confident and then the least recent."""
    if len(entries) <= cap:
        return entries
    ranked = sorted(
        entries,
        key=lambda e: (float(e.get("confidence") or 0.0), str(e.get("last_seen") or "")),
        reverse=True,
    )
    return ranked[:cap]


def upsert(entries: list[dict[str, Any]], incoming: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge *incoming* into a matching entry, or append it as a new one."""
    key = dedup_key(incoming)
    for entry in entries:
        if dedup_key(entry) == key:
            merge(entry, incoming)
            return entries
    incoming.setdefault("id", new_id())
    incoming.setdefault("first_seen", incoming.get("last_seen") or "")
    incoming["occurrences"] = int(incoming.get("occurrences") or 1)
    incoming["confidence"] = confidence_for(incoming["occurrences"])
    entries.append(incoming)
    return entries


__all__ = [
    "CONFIDENCE_CAP",
    "CONFIDENCE_DECAY",
    "DEDUP_FIELDS",
    "MAX_ENTRIES",
    "confidence_for",
    "dedup_key",
    "evict",
    "merge",
    "new_id",
    "upsert",
]
