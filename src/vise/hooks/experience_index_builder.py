#!/usr/bin/env python3
"""Build (or rebuild) the experience memory sidecar index.

Called as a detached subprocess by experience_injector.py when the index
is absent or stale.  Safe to run concurrently: each bucket file is written
via a PID-unique temp file + os.rename (atomic on POSIX/Linux).

Index layout under ~/.local/share/vise/experience_index/:
  meta.json                  -- {store_mtime, entry_count}
  score/P_<key>.json         -- slim score entries grouped by pattern's parent dir
  detail/P_<key>.json        -- output fields (description, resolution, occurrences)
                                parallel to the score file (same index = same entry)

Parent dir key encoding:  "/" → "_",  "." → "DOT"
  "src/vise/cli"  → "P_src_vise_cli"
  "."            → "P_DOT"          (root patterns — always loaded at query time)

Why parent-dir bucketing (not extension bucketing)
--------------------------------------------------
The original scoring uses a parent-directory fallback: if a glob pattern does
not fullmatch the target path, but the pattern's parent dir equals the target's
parent dir, path_score = 0.7.  This means "src/vise/cli/*.py" scores well
against "src/vise/cli/run_cmd.ts" (different extension, same dir).  An
extension-keyed index silently drops these cross-extension matches.

Parent-dir bucketing is correct: load the target's parent-dir bucket (which
contains ALL patterns whose parent dir matches, regardless of extension) plus
every ANCESTOR bucket up to the root "." bucket.  This gives a handful of
buckets per query instead of a 3169-entry scan.

Why the ancestors and not just the immediate parent
---------------------------------------------------
A `*` in a glob spans `/`, so a pattern anchored at an ancestor directory can
still fullmatch the target: `src/vise/*` matches `src/vise/cli/run_cmd.py`
with path_score = 1.0, the maximum.  Its parent dir is `src/vise`, so loading
only `src/vise/cli` dropped it — and dropping it dropped the *best* candidate,
not a marginal one.  Every pattern that can fullmatch a target is anchored at
some ancestor of that target, so walking the ancestor chain is exact, and the
chain is bounded by path depth.

Two buckets escape that reasoning and are therefore always loaded:

  _nopattern  entries with no `file_pattern` at all.  They still score on
              keywords, domain and confidence, so the full scan surfaces them;
              the index used to write this bucket and then never read it.
  _wildcard   patterns whose *parent* contains a `*` (`src/*/cli/*.py`).  Their
              parent is not a literal directory, so no ancestor key can name
              them.  The recorder does not currently emit these, which is
              exactly why they need somewhere to go: a hole nobody fills is a
              hole nobody notices.

Rebuild concurrency safety:
  meta.lock prevents duplicate spawns (TTL = 30 s).
  Each file written via PID-unique temp + os.rename (atomic on POSIX/Linux).
"""

import json
import os
import time
from collections import defaultdict
from pathlib import Path

from vise.hooks import _xdg

# Fields written to score files — the only data read at query time.
# project_origin is needed here (not detail) because ranking happens before
# the top-3 cut — see experience_injector._project_multiplier.
_SCORE_FIELDS = frozenset({"file_pattern", "keywords", "domain", "confidence", "project_origin"})

# Fields written to detail files — read only for the final top-3 winners.
_DETAIL_FIELDS = frozenset({"description", "resolution", "occurrences"})

LOCK_TTL = 30.0

# Bump when the score/detail field split OR the bucketing changes — forces
# existing indexes (built under the old schema) to rebuild instead of being
# read stale. Mirrored in experience_injector.SCHEMA_VERSION.
SCHEMA_VERSION = 3

# Buckets that no ancestor key can name, so the reader always loads them.
# Mirrored in experience_injector.
NOPATTERN_KEY = "P__nopattern"
WILDCARD_KEY = "P__wildcard"


def _store_path() -> Path:
    return _xdg.experience_memory_path()


def _index_dir() -> Path:
    return _xdg.experience_index_dir()


def parent_to_key(parent: str) -> str:
    """Encode a parent-dir path into a safe filename key."""
    return "P_" + parent.replace("/", "_").replace(".", "DOT")


def bucket_key(pattern: str) -> str:
    """Which bucket file an entry's *pattern* belongs in.

    Not the same question as `parent_to_key`: the entry still records its true
    `_parent` for the 0.7 same-directory fallback, but a pattern whose parent
    is not a literal directory cannot be found by walking a target's ancestors,
    so it is filed where the reader always looks.
    """
    if not pattern:
        return NOPATTERN_KEY
    parent = str(Path(pattern).parent)
    if "*" in parent:
        return WILDCARD_KEY
    return parent_to_key(parent)


def _write_atomic(path: Path, data: object, pid: int) -> None:
    tmp = path.with_name(path.name + f".{pid}.tmp")
    tmp.write_bytes(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    os.rename(tmp, path)


def build(store: Path, idx_dir: Path) -> None:
    """Partition *store* entries into per-parent-dir score/detail bucket files."""
    idx_dir.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()

    lock = idx_dir / "meta.lock"
    if lock.exists():
        try:
            if time.time() - lock.stat().st_mtime < LOCK_TTL:
                return
        except OSError:
            pass
    try:
        lock.touch()
    except OSError:
        pass

    try:
        raw = store.read_bytes()
        entries = json.loads(raw).get("entries", [])
    except Exception:
        return

    # score_buckets[key] and detail_buckets[key] are strictly parallel lists:
    # score_buckets[key][i] corresponds to detail_buckets[key][i].
    score_buckets: dict[str, list] = defaultdict(list)
    detail_buckets: dict[str, list] = defaultdict(list)

    for e in entries:
        pattern = e.get("file_pattern", "")
        parent = str(Path(pattern).parent) if pattern else "_nopattern"
        key = bucket_key(pattern)

        score_entry = {k: e[k] for k in _SCORE_FIELDS if k in e}
        score_entry["_parent"] = parent  # pre-baked for O(1) parent comparison
        score_buckets[key].append(score_entry)

        detail_buckets[key].append({k: e[k] for k in _DETAIL_FIELDS if k in e})

    score_dir = idx_dir / "score"
    detail_dir = idx_dir / "detail"
    score_dir.mkdir(exist_ok=True)
    detail_dir.mkdir(exist_ok=True)

    for key, bucket in score_buckets.items():
        _write_atomic(score_dir / f"{key}.json", bucket, pid)
    for key, bucket in detail_buckets.items():
        _write_atomic(detail_dir / f"{key}.json", bucket, pid)

    _write_atomic(
        idx_dir / "meta.json",
        {
            "store_mtime": store.stat().st_mtime,
            "entry_count": len(entries),
            "schema_version": SCHEMA_VERSION,
        },
        pid,
    )

    try:
        lock.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> None:
    build(_store_path(), _index_dir())


if __name__ == "__main__":
    main()
