#!/usr/bin/env python3
"""Experience Memory Injector — PreToolUse hook for Write/Edit.

Reads experience memory JSONs and injects relevant memories as context
when the agent modifies files. Self-contained (no MCP imports).

Protocol (same as graph_enforcer.py):
  stdin:  {"tool_name": "Write", ...}
  env:    FILE (path being modified), CLAUDE_PROJECT_DIR
  stdout: {"decision": "approve"}  (never blocks)
  stderr: experience memory context (visible to agent)
  exit 0: always

Performance design
------------------
Baseline (O(N) full scan): p50 ~274 ms for 3169 entries.
Target: p50 < 60 ms.

Hot path (index warm, ~55 ms p50):
  1. Import stdlib + inlined helpers (no sys.path.insert)   ~3 ms
  2. Load meta.json (mtime check)                           ~0.1 ms
  3. Load score/P_<parent>.json + score/P_DOT.json          ~0.4 ms
  4. Score ~150 candidates with _fast_glob_match            ~0.5 ms
  5. Load detail entry for each top-3 winner                ~0.1 ms
  Total logic: ~4 ms + ~48 ms CPython startup floor

Cold path (index absent or stale):
  Spawn experience_index_builder.py detached; fall back to full store scan.
  Next invocation hits the warm index.

Index layout: ~/.local/share/vise/experience_index/
  meta.json              — {store_mtime, entry_count}
  score/P_<key>.json     — slim scoring entries grouped by pattern parent dir
  detail/P_<key>.json    — output fields (description, resolution, occurrences);
                           strictly parallel to the score file (same i = same entry)

Parent-dir key:  "/" → "_",  "." → "DOT"   e.g. "src/jig/cli" → "P_src_jig_cli"
Root patterns (parent=".") are written to score/P_DOT.json and always loaded.

Why parent-dir (not extension-keyed)
-------------------------------------
The scoring formula has a parent-directory fallback: if a glob pattern does not
fullmatch the target but shares the same parent dir, path_score = 0.7.  This
means "src/jig/cli/*.py" scores well against "src/jig/cli/run_cmd.ts".
An extension-keyed index silently misses these cross-extension matches.
Parent-dir bucketing is both correct and smaller (~150 candidates vs 3169).

Rebuild concurrency safety:
  meta.lock prevents duplicate spawns (TTL = 30 s).
  Each bucket file written via PID-unique temp + os.rename (atomic POSIX).

Key optimisations vs original O(N) scan:
  1. Parent-dir index: score ~150 entries instead of 3169 (95% fewer)
  2. Parallel score/detail split: hot path reads ~5 KB not 3.9 MB
  3. _fast_glob_match: O(1) string ops vs re.compile/fullmatch per entry
  4. Pre-baked _parent: no Path() construction in the score loop
  5. Inlined _common helpers: eliminates sys.path.insert + module read (~7 ms)
"""

import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Inlined from _common.py — avoids sys.path.insert + module-file read (~7 ms).
# Keep in sync with src/vise/hooks/_common.py.
# ---------------------------------------------------------------------------
_DOMAIN_MAP: dict[str, list[str]] = {
    "auth":   ["auth", "login", "session", "token", "jwt"],
    "api":    ["api", "endpoint", "route", "controller", "handler", "middleware"],
    "ui":     ["component", "page", "view", "layout", "modal", "form", "panel"],
    "config": ["config", "setting", "env", "constant"],
    "data":   ["model", "schema", "entity", "migration", "repository", "store"],
    "style":  ["style", "css", "theme"],
    "util":   ["util", "helper", "lib", "common", "shared"],
}


def _extract_keywords(path: str) -> list[str]:
    stem = Path(path).stem.lower()
    words = re.split(r"(?<=[a-z])(?=[A-Z])|[-_./\\]", stem)
    words = [w.lower() for w in words if len(w) > 1]
    parent = Path(path).parent.name.lower()
    if parent and len(parent) > 1 and parent not in (".", "src"):
        words.append(parent)
    return list(dict.fromkeys(words))


def _guess_domain(path: str) -> str:
    lower = path.lower()
    best, best_score = "", 0
    for domain, kws in _DOMAIN_MAP.items():
        score = sum(1 for kw in kws if kw in lower)
        if score > best_score:
            best_score = score
            best = domain
    return best or "general"


# ---------------------------------------------------------------------------
# Fast glob matcher — no re.compile per entry.
# All patterns in the real store are single-wildcard ("path/to/*.ext").
# ---------------------------------------------------------------------------

def _fast_glob_match(pattern: str, target: str) -> bool:
    """Return True if *target* matches *pattern* (glob-style, * = anything).

    Single-wildcard handled in O(1) with str.startswith/endswith.
    Multi-wildcard (absent in practice) falls back to regex.
    """
    if "*" not in pattern:
        return pattern == target
    parts = pattern.split("*")
    if len(parts) == 2:
        prefix, suffix = parts
        return (
            target.startswith(prefix)
            and target.endswith(suffix)
            and len(target) >= len(prefix) + len(suffix)
        )
    try:
        return bool(re.fullmatch(pattern.replace("*", ".*"), target))
    except re.error:
        return False


# ---------------------------------------------------------------------------
# Index helpers
# ---------------------------------------------------------------------------

def _index_dir() -> Path:
    return Path.home() / ".local" / "share" / "vise" / "experience_index"


def _store_path() -> Path:
    return Path.home() / ".local" / "share" / "vise" / "experience_memory.json"


def _parent_key(parent: str) -> str:
    """Encode a parent-dir path as a safe filename key (mirrors builder)."""
    return "P_" + parent.replace("/", "_").replace(".", "DOT")


def _index_is_fresh(idx_dir: Path, store: Path) -> bool:
    meta_path = idx_dir / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_bytes())
        return abs(meta.get("store_mtime", 0) - store.stat().st_mtime) < 0.001
    except Exception:
        return False


def _spawn_rebuild(idx_dir: Path) -> None:
    """Spawn the index builder detached. Uses meta.lock to avoid pile-on."""
    import subprocess
    import time

    lock = idx_dir / "meta.lock"
    try:
        idx_dir.mkdir(parents=True, exist_ok=True)
        if lock.exists() and time.time() - lock.stat().st_mtime < 30.0:
            return
    except OSError:
        pass

    builder = Path(__file__).parent / "experience_index_builder.py"
    if not builder.exists():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(builder)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        pass


def _load_index_candidates(
    idx_dir: Path, target_parent: str
) -> tuple[list[dict], list[dict]]:
    """Load (score_entries, detail_entries) for *target_parent* from the index.

    Loads the target's parent-dir bucket plus the root "." bucket (P_DOT),
    which contains patterns like "*.ts" that can match any path.
    score[i] and detail[i] are strictly parallel within each bucket.
    """
    score_dir = idx_dir / "score"
    detail_dir = idx_dir / "detail"

    score_flat: list[dict] = []
    detail_flat: list[dict] = []

    keys = [_parent_key(target_parent)]
    if target_parent != ".":
        keys.append("P_DOT")

    for key in keys:
        sp = score_dir / f"{key}.json"
        dp = detail_dir / f"{key}.json"
        if not sp.exists():
            continue
        try:
            sb: list[dict] = json.loads(sp.read_bytes())
        except Exception:
            continue
        try:
            db: list[dict] = json.loads(dp.read_bytes()) if dp.exists() else []
        except Exception:
            db = []

        score_flat += sb
        detail_flat += db
        # Guard against builder/reader race producing length mismatch
        shortfall = len(sb) - len(db)
        if shortfall > 0:
            detail_flat += [{}] * shortfall

    return score_flat, detail_flat


def _load_entries_from_store(store: Path) -> list[dict]:
    """Full store load — fallback when index is not ready."""
    if not store.exists():
        return []
    try:
        return json.loads(store.read_bytes()).get("entries", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Scoring — identical formula to original
# ---------------------------------------------------------------------------

def _score_entry(
    entry: dict,
    target_path: str,
    target_kws: set[str],
    target_domain: str,
    target_parent: str,
) -> float:
    """Compute relevance score (same weights as original).

    Uses _fast_glob_match and pre-baked _parent to avoid per-entry
    re.compile and Path() calls.  Formula unchanged.
    """
    pattern = entry.get("file_pattern", "")
    path_score = 0.0
    if pattern:
        if _fast_glob_match(pattern, target_path):
            path_score = 1.0
        else:
            parent = entry.get("_parent") or str(Path(pattern).parent)
            if parent == target_parent:
                path_score = 0.7

    entry_kws = set(entry.get("keywords", []))
    kw_score = 0.0
    if entry_kws and target_kws:
        kw_score = len(entry_kws & target_kws) / len(entry_kws | target_kws)

    domain_score = 1.0 if entry.get("domain") == target_domain else 0.0
    conf = entry.get("confidence", 0.3)

    return path_score * 0.30 + kw_score * 0.25 + domain_score * 0.20 + conf * 0.15


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    approve = json.dumps({"decision": "approve"})

    try:
        hook_input = json.load(sys.stdin)
    except Exception:
        print(approve)
        return

    file_path = os.environ.get("FILE", "")
    if not file_path:
        tool_input = hook_input.get("tool_input", {})
        file_path = tool_input.get("file_path", tool_input.get("path", ""))

    if not file_path:
        print(approve)
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    project_name = Path(project_dir).name if project_dir else ""

    target_kws = set(_extract_keywords(file_path))
    target_domain = _guess_domain(file_path)
    target_parent = str(Path(file_path).parent)

    store = _store_path()
    idx_dir = _index_dir()

    # ---- candidate selection -----------------------------------------------
    if _index_is_fresh(idx_dir, store):
        # Hot path: load ~5 KB of score data, score ~150 entries.
        score_entries, detail_entries = _load_index_candidates(idx_dir, target_parent)
        using_index = True
    else:
        # Cold path: full store scan; trigger background rebuild for next call.
        _spawn_rebuild(idx_dir)
        score_entries = _load_entries_from_store(store)
        detail_entries = []
        using_index = False

    # Per-project entries always from their own small store (appended as-is;
    # they carry all fields so detail lookup is not needed for them).
    proj_extra: list[dict] = []
    if project_name:
        proj_store = (
            Path.home() / ".local" / "share" / "vise"
            / "project_memories" / project_name / "experience_memory.json"
        )
        if proj_store.exists():
            try:
                proj_extra = json.loads(proj_store.read_bytes()).get("entries", [])
            except Exception:
                pass

    if not score_entries and not proj_extra:
        print(approve)
        return

    # ---- scoring ------------------------------------------------------------
    # (score_entry, score_value, detail_index)
    # detail_index: position in detail_entries for index entries,
    #               or -1 for cold-path / project entries (full fields present).
    scored: list[tuple[dict, float, int]] = []

    for i, entry in enumerate(score_entries):
        s = _score_entry(entry, file_path, target_kws, target_domain, target_parent)
        if s > 0.10:
            scored.append((entry, s, i if using_index else -1))

    for entry in proj_extra:
        s = _score_entry(entry, file_path, target_kws, target_domain, target_parent)
        if s > 0.10:
            scored.append((entry, s, -1))

    scored.sort(key=lambda x: x[1], reverse=True)
    top3 = scored[:3]

    if not top3:
        print(approve)
        return

    # ---- output -------------------------------------------------------------
    filename = Path(file_path).name
    lines = [
        f"⚡ Experience Memory "
        f"({len(top3)} match{'es' if len(top3) > 1 else ''} for {filename}):"
    ]
    for entry, score, detail_idx in top3:
        if detail_idx >= 0 and detail_idx < len(detail_entries):
            detail = detail_entries[detail_idx]
        else:
            detail = entry  # cold path or project entry — all fields present

        occurrences = detail.get("occurrences", 1)
        desc = detail.get("description", "")[:80]
        resolution = detail.get("resolution", "")
        lines.append(f"  [{score:.2f}] {desc} ({occurrences}x)")
        if resolution:
            lines.append(f"    → {resolution[:100]}")

    print("\n".join(lines), file=sys.stderr)
    print(approve)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"decision": "approve"}))
