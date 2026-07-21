"""experience_gc — scored garbage collection for the experience memory store.

Pipeline (applied in order):
  1. consolidate(entries) — merge near-duplicates sharing (type, file_pattern,
     domain) with difflib description similarity ≥ 0.85.
  2. score_entry(entry, now) — age-decayed score with confirmation boost and
     superseded penalty.
  3. Drop entries whose score falls below *threshold*, except those seen
     within the last *keep_recent_days* (default 30) and protected ids.

Protection: any experience id referenced in <project>/.vise/asset_journal.jsonl
as ``experience_refs`` is never dropped.

Public API
----------
score_entry(entry, now)       -> float
consolidate(entries)          -> (kept, merged_map)
protected_ids_for(project_dir) -> set[str]
gc(store_path, *, apply, protected_ids, threshold, keep_recent_days) -> report
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HALF_LIFE_DAYS: float = 90.0          # kept for backward-compat; GC now uses FSRS
CONFIRMATION_WEIGHT: float = 0.3      # occurrences/confidence boost coefficient
SUPERSEDED_PENALTY: float = 0.4       # multiplier on superseded entries
DEFAULT_THRESHOLD: float = 0.10       # entries below this are dropped
DEFAULT_KEEP_RECENT_DAYS: int = 30    # entries seen within N days are kept regardless
CONSOLIDATE_SIMILARITY: float = 0.85  # difflib ratio for near-duplicate merging
AUTO_GC_NUDGE_THRESHOLD: int = 5_000  # entry count at which to emit the stderr nudge


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_entry(entry: dict[str, Any], now: datetime) -> float:
    """Compute a retention score in [0, 1] for one entry.

    Components:
      - Age decay: FSRS retrievability R(t, S) using per-entry stability and
        last_reviewed (falls back to last_seen for pre-FSRS records).
        Unified model: same curve as compute_relevance in experience_memory.py.
      - Confirmation boost: small addition from occurrences and confidence.
      - Superseded penalty: entries flagged as superseded are multiplied down.

    The "superseded" flag is set externally by consolidate() — we look for
    a ``_superseded`` key in the dict.
    """
    import math

    from vise.engines.fsrs import DEFAULT_STABILITY_DAYS, retrievability

    # ── FSRS age decay ────────────────────────────────────────────────────────
    # Prefer last_reviewed (recall-time anchor) over last_seen (write-time).
    anchor_raw: str = (
        entry.get("last_reviewed")
        or entry.get("last_seen")
        or entry.get("first_seen")
        or ""
    )
    stability: float = float(entry.get("stability") or 0.0)
    if stability <= 0.0:
        stability = DEFAULT_STABILITY_DAYS

    age_score: float = 0.5  # default for unknown age
    if anchor_raw:
        try:
            anchor = datetime.fromisoformat(anchor_raw)
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            now_tz = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            days = max(0.0, (now_tz - anchor).total_seconds() / 86400.0)
            age_score = retrievability(days, stability)
        except (ValueError, TypeError):
            age_score = 0.5

    # ── Confirmation boost ─────────────────────────────────────────────────────
    occurrences: int = int(entry.get("occurrences") or 1)
    confidence: float = float(entry.get("confidence") or 0.3)
    # Logarithmic growth so 1 occurrence → small boost, 10 → moderate, 100+ → capped
    occurrence_factor = math.log1p(occurrences) / math.log1p(20)  # normalised to ~1 at 20
    confirmation = CONFIRMATION_WEIGHT * min(1.0, occurrence_factor * confidence)

    raw_score = age_score + confirmation

    # ── Superseded penalty ─────────────────────────────────────────────────────
    if entry.get("_superseded"):
        raw_score *= SUPERSEDED_PENALTY

    return min(1.0, raw_score)


# ---------------------------------------------------------------------------
# Near-duplicate detection / consolidation
# ---------------------------------------------------------------------------

def _description_similarity(a: str, b: str) -> float:
    """difflib SequenceMatcher ratio between two description strings."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dedup_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("type") or ""),
        str(entry.get("file_pattern") or ""),
        str(entry.get("domain") or ""),
    )


def consolidate(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Merge near-duplicates; return (kept_entries, merged_map).

    Near-duplicate = same (type, file_pattern, domain) AND
    description similarity ≥ CONSOLIDATE_SIMILARITY.

    Merge strategy:
      - Keep the newest entry (latest last_seen / highest confidence).
      - Sum occurrences across all duplicates.
      - Take max confidence.
      - Append distinct resolution texts separated by " | ".
      - Mark older entries with ``_superseded=True``.

    merged_map: {old_id -> kept_id}  (old_ids that were absorbed)
    """
    if not entries:
        return [], {}

    # Sort by confidence desc then last_seen desc so the "best" entry comes first
    sorted_entries = sorted(
        entries,
        key=lambda e: (float(e.get("confidence") or 0), str(e.get("last_seen") or "")),
        reverse=True,
    )

    kept: list[dict[str, Any]] = []
    merged_map: dict[str, str] = {}

    # We'll accumulate kept entries and try to merge each new candidate into
    # an existing kept entry.
    for candidate in sorted_entries:
        c_key = _dedup_key(candidate)
        c_desc = str(candidate.get("description") or "")
        c_id = str(candidate.get("id") or "")

        matched = False
        for existing in kept:
            if _dedup_key(existing) != c_key:
                continue
            e_desc = str(existing.get("description") or "")
            if _description_similarity(c_desc, e_desc) < CONSOLIDATE_SIMILARITY:
                continue

            # Merge candidate into existing
            e_id = str(existing.get("id") or "")
            existing["occurrences"] = int(existing.get("occurrences") or 1) + int(candidate.get("occurrences") or 1)
            existing["confidence"] = max(
                float(existing.get("confidence") or 0),
                float(candidate.get("confidence") or 0),
            )
            # Keep newest last_seen
            e_ls = str(existing.get("last_seen") or "")
            c_ls = str(candidate.get("last_seen") or "")
            if c_ls > e_ls:
                existing["last_seen"] = c_ls
            # Keep earliest first_seen
            e_fs = str(existing.get("first_seen") or "")
            c_fs = str(candidate.get("first_seen") or "")
            if c_fs and (not e_fs or c_fs < e_fs):
                existing["first_seen"] = c_fs
            # Merge resolution
            e_res = str(existing.get("resolution") or "").strip()
            c_res = str(candidate.get("resolution") or "").strip()
            if c_res and c_res not in e_res:
                existing["resolution"] = (e_res + " | " + c_res).strip(" |")

            if c_id and e_id and c_id != e_id:
                merged_map[c_id] = e_id

            matched = True
            break

        if not matched:
            # Deep copy so we don't mutate caller's data
            kept.append(dict(candidate))

    # Mark superseded entries: an entry is superseded if same dedup_key exists
    # in kept with higher confidence AND a newer last_seen, and the entry itself
    # has lower confidence. This detects entries that weren't merged (different
    # description) but are effectively outdated.
    key_to_best: dict[tuple, dict] = {}
    for e in kept:
        k = _dedup_key(e)
        if k not in key_to_best:
            key_to_best[k] = e
        else:
            best = key_to_best[k]
            if float(e.get("confidence") or 0) > float(best.get("confidence") or 0):
                key_to_best[k] = e

    for e in kept:
        k = _dedup_key(e)
        best = key_to_best[k]
        if best is not e:
            # This entry has the same key but lower confidence → superseded
            e_ls = str(e.get("last_seen") or "")
            b_ls = str(best.get("last_seen") or "")
            if b_ls >= e_ls:
                e["_superseded"] = True

    return kept, merged_map


# ---------------------------------------------------------------------------
# Protected-ids helper
# ---------------------------------------------------------------------------

def protected_ids_for(project_dir: str | Path) -> set[str]:
    """Collect experience ids referenced in <project_dir>/.vise/asset_journal.jsonl.

    Reads lines of JSONL looking for any ``experience_refs`` key (list of
    id strings).  Returns the union of all referenced ids.

    If the journal does not exist or cannot be read, returns an empty set
    (GC skips protection for that project — document this in the report).
    """
    journal = Path(project_dir) / ".vise" / "asset_journal.jsonl"
    ids: set[str] = set()
    if not journal.exists():
        return ids
    try:
        for line in journal.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                refs = record.get("experience_refs")
                if isinstance(refs, list):
                    for ref in refs:
                        if isinstance(ref, str) and ref:
                            ids.add(ref)
                elif isinstance(refs, str) and refs:
                    ids.add(refs)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return ids


# ---------------------------------------------------------------------------
# GC pipeline
# ---------------------------------------------------------------------------

def gc(
    store_path: str | Path,
    *,
    apply: bool = False,
    protected_ids: set[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    keep_recent_days: int = DEFAULT_KEEP_RECENT_DAYS,
) -> dict[str, Any]:
    """Run the garbage collection pipeline on one experience store file.

    Parameters
    ----------
    store_path:
        Absolute path to the ``experience_memory.json`` file.
    apply:
        If False (default), dry-run — compute the report but write nothing.
        If True, rewrite the store atomically (temp+rename) and place a
        ``.bak`` alongside.
    protected_ids:
        Set of entry ids that must never be dropped regardless of score.
        Caller should collect these via ``protected_ids_for(project_dir)``
        or pass an empty set to skip protection.
    threshold:
        Minimum retention score.  Entries below this are dropped unless
        they were seen within *keep_recent_days* or are protected.
    keep_recent_days:
        Entries seen within this many days are retained unconditionally,
        regardless of score.

    Returns
    -------
    report dict:
        {
          "store_path": str,
          "before": int,            # entries before GC
          "after": int,             # entries after GC
          "consolidated": int,      # entries merged (absorbed)
          "dropped": int,           # entries dropped by score threshold
          "protected_kept": int,    # entries kept because of protected_ids
          "bytes_before": int,
          "bytes_after": int,       # 0 on dry-run (file not rewritten)
          "dry_run": bool,
          "error": str | None,      # set if store could not be loaded
        }
    """
    store_path = Path(store_path)
    protected_ids = protected_ids or set()
    now = datetime.now(timezone.utc)

    report: dict[str, Any] = {
        "store_path": str(store_path),
        "before": 0,
        "after": 0,
        "consolidated": 0,
        "dropped": 0,
        "protected_kept": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "dry_run": not apply,
        "error": None,
    }

    # ── Load ──────────────────────────────────────────────────────────────────
    if not store_path.exists():
        report["error"] = f"Store not found: {store_path}"
        return report

    try:
        raw = store_path.read_bytes()
        data = json.loads(raw)
        entries: list[dict] = data.get("entries", [])
    except Exception as exc:
        report["error"] = f"Failed to load store: {exc}"
        return report

    report["bytes_before"] = len(raw)
    report["before"] = len(entries)

    # ── Consolidate ───────────────────────────────────────────────────────────
    kept_after_consolidate, merged_map = consolidate(entries)
    consolidated_count = len(entries) - len(kept_after_consolidate)
    report["consolidated"] = consolidated_count

    # ── Score & filter ────────────────────────────────────────────────────────
    final_entries: list[dict] = []
    dropped_count = 0
    protected_kept_count = 0

    for e in kept_after_consolidate:
        e_id = str(e.get("id") or "")
        is_protected = e_id in protected_ids

        # Recency check: entry seen within keep_recent_days → always keep
        last_seen_raw = str(e.get("last_seen") or e.get("first_seen") or "")
        is_recent = False
        if last_seen_raw:
            try:
                ls = datetime.fromisoformat(last_seen_raw)
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                days_ago = (now - ls).total_seconds() / 86400.0
                is_recent = days_ago <= keep_recent_days
            except (ValueError, TypeError):
                pass

        score = score_entry(e, now)

        if is_protected:
            # Strip internal bookkeeping key before keeping
            e.pop("_superseded", None)
            final_entries.append(e)
            if score < threshold and not is_recent:
                protected_kept_count += 1
        elif is_recent or score >= threshold:
            e.pop("_superseded", None)
            final_entries.append(e)
        else:
            dropped_count += 1

    report["dropped"] = dropped_count
    report["protected_kept"] = protected_kept_count
    report["after"] = len(final_entries)

    # ── Write (apply mode) ────────────────────────────────────────────────────
    if apply:
        # Backup
        bak_path = store_path.with_suffix(".json.bak")
        shutil.copy2(store_path, bak_path)

        # Build new store data preserving existing metadata fields
        new_data = {k: v for k, v in data.items() if k != "entries"}
        new_data["entries"] = final_entries
        new_data["count"] = len(final_entries)

        from datetime import datetime as _dt
        new_data["last_updated"] = _dt.now().isoformat()

        new_json = json.dumps(new_data, indent=2).encode("utf-8")

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(dir=store_path.parent, suffix=".tmp")
        try:
            os.write(fd, new_json)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, store_path)

        report["bytes_after"] = len(new_json)
    else:
        # Dry-run: estimate size
        new_data = {k: v for k, v in data.items() if k != "entries"}
        new_data["entries"] = final_entries
        report["bytes_after"] = len(json.dumps(new_data, indent=2).encode("utf-8"))

    return report


# ---------------------------------------------------------------------------
# Auto-GC nudge (called from ExperienceMemoryStore.save)
# ---------------------------------------------------------------------------

def maybe_nudge_gc(entry_count: int, store_path: str | Path | None = None) -> None:
    """Print a one-line stderr nudge when entry_count exceeds AUTO_GC_NUDGE_THRESHOLD.

    No deletion, no mutation.  V1 policy: explicit GC only.
    """
    import sys
    if entry_count > AUTO_GC_NUDGE_THRESHOLD:
        path_hint = f" ({store_path})" if store_path else ""
        print(
            f"experience store has {entry_count} entries{path_hint}"
            " — run `vise experience gc` to curate",
            file=sys.stderr,
        )
