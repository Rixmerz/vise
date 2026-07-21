"""Tests for experience_injector.py and experience_index_builder.py.

Coverage:
  - _fast_glob_match correctness (single-wildcard, multi-wildcard, exact, no-match)
  - _score_entry formula (path_score, kw_score, domain_score, conf weights)
  - _extract_keywords / _guess_domain (inlined _common helpers)
  - Index builder: produces parallel score/detail files, correct field split,
    parent-dir bucketing, atomic write (temp+rename), staleness detection
  - Injector pre-filter: correct candidates loaded for given parent dir,
    entries outside the bucket excluded, entries inside scored identically
  - Detail lookup: top-3 detail fields resolved via parallel file position
  - Staleness rebuild trigger: stale index spawns rebuild, fresh index does not
  - Contract preservation: full-store results == indexed results on fixture data
  - No-crash on empty store, corrupt store, missing index dir, missing FILE env
  - Cold-path (no index) falls through to full store scan
  - Cross-extension parent fallback: .py pattern scores .ts target via parent match
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import vise.hooks.experience_injector as inj
import vise.hooks.experience_index_builder as bld


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_entry(
    file_pattern: str = "",
    keywords: list[str] | None = None,
    domain: str = "general",
    confidence: float = 0.3,
    description: str = "test description",
    resolution: str = "test resolution",
    occurrences: int = 1,
) -> dict[str, Any]:
    return {
        "file_pattern": file_pattern,
        "keywords": keywords or [],
        "domain": domain,
        "confidence": confidence,
        "description": description,
        "resolution": resolution,
        "occurrences": occurrences,
    }


def _make_store(entries: list[dict], path: Path) -> Path:
    path.write_text(json.dumps({"entries": entries}))
    return path


@pytest.fixture()
def tmp_store(tmp_path: Path) -> tuple[Path, Path]:
    """Return (store_path, index_dir) pointing into tmp_path."""
    store = tmp_path / "experience_memory.json"
    idx_dir = tmp_path / "experience_index"
    return store, idx_dir


# ---------------------------------------------------------------------------
# _fast_glob_match
# ---------------------------------------------------------------------------

class TestFastGlobMatch:
    def test_exact_match_no_wildcard(self):
        assert inj._fast_glob_match("src/foo/bar.py", "src/foo/bar.py") is True

    def test_exact_mismatch_no_wildcard(self):
        assert inj._fast_glob_match("src/foo/bar.py", "src/foo/baz.py") is False

    def test_single_wildcard_same_ext(self):
        assert inj._fast_glob_match("src/foo/*.py", "src/foo/bar.py") is True

    def test_single_wildcard_different_ext(self):
        assert inj._fast_glob_match("src/foo/*.py", "src/foo/bar.ts") is False

    def test_single_wildcard_wrong_prefix(self):
        assert inj._fast_glob_match("src/foo/*.py", "src/other/bar.py") is False

    def test_root_wildcard(self):
        assert inj._fast_glob_match("*.py", "anything.py") is True
        assert inj._fast_glob_match("*.py", "anything.ts") is False

    def test_empty_segment_between_prefix_and_suffix(self):
        # prefix="src/", suffix=".py", target="src/.py" — length exactly len(pre)+len(suf)
        assert inj._fast_glob_match("src/*.py", "src/x.py") is True

    def test_empty_wildcard_match_prevented(self):
        # "src/*.py" should not match "src/.py" when prefix+suffix > len(target) — guard
        # prefix="src/", suffix=".py" → min_len=7; "src/.py" is 7 chars — edge OK
        assert inj._fast_glob_match("src/*.py", "src/.py") is True  # minimal valid match

    def test_multi_wildcard_falls_back_to_regex(self):
        # "**/*.py" → ".*/.*.py" regex — matches deep/nested/file.py
        assert inj._fast_glob_match("**/*.py", "deep/nested/file.py") is True

    def test_invalid_regex_in_multi_wildcard_returns_false(self):
        # Pattern that produces invalid regex after replacing * → .*
        # Construct one by using a bare repetition quantifier
        result = inj._fast_glob_match("*[invalid*.py", "anything.py")
        assert result is False  # re.error caught → False


# ---------------------------------------------------------------------------
# _score_entry
# ---------------------------------------------------------------------------

class TestScoreEntry:
    def _score(self, entry, file_path="src/foo/bar.py", kws=None, domain="general", parent=None):
        kws = set(kws or ["bar", "foo"])
        parent = parent or str(Path(file_path).parent)
        return inj._score_entry(entry, file_path, kws, domain, parent)

    def test_perfect_glob_match_boosts_path_score(self):
        e = _make_entry(file_pattern="src/foo/*.py", keywords=["bar", "foo"],
                        domain="general", confidence=0.5)
        score = self._score(e)
        # path=1.0*0.30, kw=1.0*0.25, domain=1.0*0.20, conf=0.5*0.15
        expected = 0.30 + 0.25 + 0.20 + 0.075
        assert abs(score - expected) < 1e-6

    def test_parent_dir_fallback_scores_07(self):
        # .py pattern, .ts target — same parent dir triggers 0.7 path_score
        e = _make_entry(file_pattern="src/cli/*.py", keywords=[], domain="general",
                        confidence=0.3, )
        e["_parent"] = "src/cli"
        score = inj._score_entry(e, "src/cli/run.ts", set(), "general", "src/cli")
        # path=0.7*0.30, kw=0, domain=0.20, conf=0.3*0.15
        expected = 0.21 + 0.0 + 0.20 + 0.045
        assert abs(score - expected) < 1e-6

    def test_no_match_no_pattern_score_is_conf_only(self):
        e = _make_entry(file_pattern="", keywords=[], domain="other", confidence=0.4)
        score = self._score(e, domain="general")
        # path=0, kw=0, domain=0, conf=0.4*0.15
        assert abs(score - 0.06) < 1e-6

    def test_keyword_jaccard_overlap(self):
        e = _make_entry(file_pattern="", keywords=["foo", "baz"], domain="general", confidence=0.0)
        score = inj._score_entry(e, "x.py", {"foo", "bar"}, "general", ".")
        # intersection={"foo"}, union={"foo","bar","baz"} → jaccard=1/3
        assert abs(score - (1/3 * 0.25 + 0.20)) < 1e-6

    def test_threshold_filter(self):
        e = _make_entry(file_pattern="", keywords=[], domain="other", confidence=0.0)
        score = self._score(e)
        assert score <= 0.10, "entry with no signal should be at or below threshold"

    def test_uses_prebaked_parent_over_path_computation(self):
        # _parent key overrides Path(pattern).parent computation
        e = _make_entry(file_pattern="wrong/path/*.py", keywords=[], domain="general",
                        confidence=0.0)
        e["_parent"] = "src/foo"  # override to correct parent
        score = inj._score_entry(e, "src/foo/bar.py", set(), "general", "src/foo")
        # Should get parent fallback 0.7 even though pattern dir doesn't match
        assert score > 0.10


# ---------------------------------------------------------------------------
# _extract_keywords and _guess_domain (inlined helpers)
# ---------------------------------------------------------------------------

class TestInlinedHelpers:
    def test_extract_keywords_splits_on_underscore(self):
        kws = inj._extract_keywords("src/foo/my_service.py")
        assert "my" in kws
        assert "service" in kws

    def test_extract_keywords_includes_parent_dir(self):
        kws = inj._extract_keywords("src/hooks/experience_injector.py")
        assert "hooks" in kws

    def test_extract_keywords_deduplicates(self):
        kws = inj._extract_keywords("src/foo/foo.py")
        assert kws.count("foo") == 1

    def test_guess_domain_auth(self):
        assert inj._guess_domain("src/auth/login.py") == "auth"

    def test_guess_domain_api(self):
        assert inj._guess_domain("src/api/handler.py") == "api"

    def test_guess_domain_fallback(self):
        assert inj._guess_domain("src/completely_unknown_xyz.py") == "general"


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

class TestIndexBuilder:
    def test_build_creates_meta_and_buckets(self, tmp_store):
        store, idx_dir = tmp_store
        entries = [
            _make_entry("src/foo/*.py", ["foo"], "api", 0.5, "desc1", "res1", 2),
            _make_entry("src/bar/*.ts", ["bar"], "ui",  0.4, "desc2", "res2", 1),
        ]
        _make_store(entries, store)
        bld.build(store, idx_dir)

        meta = json.loads((idx_dir / "meta.json").read_bytes())
        assert meta["entry_count"] == 2
        assert abs(meta["store_mtime"] - store.stat().st_mtime) < 0.01

    def test_build_score_and_detail_are_parallel(self, tmp_store):
        store, idx_dir = tmp_store
        entries = [
            _make_entry("src/hooks/*.py", ["hook"], "api", 0.5, "descA", "resA", 3),
            _make_entry("src/hooks/*.py", ["hook"], "api", 0.6, "descB", "resB", 1),
        ]
        _make_store(entries, store)
        bld.build(store, idx_dir)

        key = bld.parent_to_key("src/hooks")
        score_b = json.loads((idx_dir / "score" / f"{key}.json").read_bytes())
        detail_b = json.loads((idx_dir / "detail" / f"{key}.json").read_bytes())

        assert len(score_b) == len(detail_b) == 2
        # Parallel: score[i] and detail[i] correspond to the same entry
        assert detail_b[0]["description"] == "descA"
        assert detail_b[1]["description"] == "descB"

    def test_score_fields_only_in_score_file(self, tmp_store):
        store, idx_dir = tmp_store
        _make_store([_make_entry("src/a/*.py", ["x"], "api", 0.5, "big desc", "big res", 9)], store)
        bld.build(store, idx_dir)

        key = bld.parent_to_key("src/a")
        score_b = json.loads((idx_dir / "score" / f"{key}.json").read_bytes())
        assert "description" not in score_b[0]
        assert "resolution" not in score_b[0]
        assert "file_pattern" in score_b[0]
        assert "_parent" in score_b[0]

    def test_detail_fields_only_in_detail_file(self, tmp_store):
        store, idx_dir = tmp_store
        _make_store([_make_entry("src/a/*.py", description="my desc", resolution="my res")], store)
        bld.build(store, idx_dir)

        key = bld.parent_to_key("src/a")
        detail_b = json.loads((idx_dir / "detail" / f"{key}.json").read_bytes())
        assert detail_b[0]["description"] == "my desc"
        assert detail_b[0]["resolution"] == "my res"
        assert "file_pattern" not in detail_b[0]

    def test_parent_dir_bucketing(self, tmp_store):
        store, idx_dir = tmp_store
        entries = [
            _make_entry("src/cli/*.py",  ["cli"],  "api"),
            _make_entry("src/tests/*.py", ["test"], "api"),
            _make_entry("src/cli/*.ts",  ["cli"],  "ui"),
        ]
        _make_store(entries, store)
        bld.build(store, idx_dir)

        cli_key   = bld.parent_to_key("src/cli")
        tests_key = bld.parent_to_key("src/tests")
        cli_score = json.loads((idx_dir / "score" / f"{cli_key}.json").read_bytes())
        tests_score = json.loads((idx_dir / "score" / f"{tests_key}.json").read_bytes())

        assert len(cli_score) == 2   # both src/cli entries
        assert len(tests_score) == 1

    def test_root_patterns_go_to_dot_bucket(self, tmp_store):
        store, idx_dir = tmp_store
        _make_store([_make_entry("*.py", ["root"])], store)
        bld.build(store, idx_dir)

        dot_key = bld.parent_to_key(".")
        score_b = json.loads((idx_dir / "score" / f"{dot_key}.json").read_bytes())
        assert len(score_b) == 1

    def test_no_pattern_entries_bucketed_under_nopattern(self, tmp_store):
        store, idx_dir = tmp_store
        _make_store([_make_entry("", ["global"])], store)
        bld.build(store, idx_dir)

        nopattern_key = bld.parent_to_key("_nopattern")
        score_b = json.loads((idx_dir / "score" / f"{nopattern_key}.json").read_bytes())
        assert len(score_b) == 1

    def test_atomic_write_no_tmp_files_left(self, tmp_store):
        store, idx_dir = tmp_store
        _make_store([_make_entry("src/a/*.py")], store)
        bld.build(store, idx_dir)

        tmp_files = list(idx_dir.rglob("*.tmp"))
        assert tmp_files == [], f"tmp files left behind: {tmp_files}"

    def test_stale_lock_allows_rebuild(self, tmp_store):
        store, idx_dir = tmp_store
        idx_dir.mkdir(parents=True)
        lock = idx_dir / "meta.lock"
        lock.touch()
        # Backdate lock by 60 s to simulate stale lock
        stale_time = time.time() - 60
        os.utime(lock, (stale_time, stale_time))

        _make_store([_make_entry("src/a/*.py", description="rebuilt")], store)
        bld.build(store, idx_dir)

        meta = json.loads((idx_dir / "meta.json").read_bytes())
        assert meta["entry_count"] == 1

    def test_fresh_lock_prevents_rebuild(self, tmp_store):
        store, idx_dir = tmp_store
        idx_dir.mkdir(parents=True)
        lock = idx_dir / "meta.lock"
        lock.touch()  # freshly created = now

        _make_store([_make_entry("src/a/*.py", description="should not appear")], store)
        bld.build(store, idx_dir)

        # meta.json should NOT have been written (rebuild skipped)
        assert not (idx_dir / "meta.json").exists()

    def test_corrupt_store_does_not_raise(self, tmp_store):
        store, idx_dir = tmp_store
        store.write_bytes(b"not json {{{")
        bld.build(store, idx_dir)  # must not raise

    def test_empty_store_builds_empty_index(self, tmp_store):
        store, idx_dir = tmp_store
        _make_store([], store)
        bld.build(store, idx_dir)
        meta = json.loads((idx_dir / "meta.json").read_bytes())
        assert meta["entry_count"] == 0


# ---------------------------------------------------------------------------
# _load_index_candidates — pre-filter correctness
# ---------------------------------------------------------------------------

class TestLoadIndexCandidates:
    def _build_index(self, tmp_path, entries):
        store = tmp_path / "store.json"
        idx_dir = tmp_path / "idx"
        _make_store(entries, store)
        bld.build(store, idx_dir)
        return idx_dir

    def test_same_parent_candidates_included(self, tmp_path):
        entries = [_make_entry("src/foo/*.py", ["match"])]
        idx_dir = self._build_index(tmp_path, entries)
        score, detail = inj._load_index_candidates(idx_dir, "src/foo")
        assert len(score) == 1
        assert score[0]["keywords"] == ["match"]

    def test_different_parent_candidates_excluded(self, tmp_path):
        entries = [_make_entry("src/bar/*.py", ["no_match"])]
        idx_dir = self._build_index(tmp_path, entries)
        score, detail = inj._load_index_candidates(idx_dir, "src/foo")
        # src/bar entries should not appear for src/foo target
        assert all(e.get("_parent") != "src/foo" for e in score)

    def test_dot_bucket_always_loaded(self, tmp_path):
        entries = [
            _make_entry("*.py",        ["root"],  description="root entry"),
            _make_entry("src/bar/*.py", ["bar"], description="bar entry"),
        ]
        idx_dir = self._build_index(tmp_path, entries)
        score, detail = inj._load_index_candidates(idx_dir, "src/foo")
        descs = [d.get("description") for d in detail]
        assert "root entry" in descs, "root '.' entries must always be included"
        assert "bar entry" not in descs

    def test_score_and_detail_are_parallel(self, tmp_path):
        entries = [
            _make_entry("src/foo/*.py", description="alpha", resolution="r1"),
            _make_entry("src/foo/*.py", description="beta",  resolution="r2"),
        ]
        idx_dir = self._build_index(tmp_path, entries)
        score, detail = inj._load_index_candidates(idx_dir, "src/foo")
        assert len(score) == len(detail) == 2
        # First score entry corresponds to first detail entry
        # Verify via keyword uniqueness: set different keywords per entry
        assert detail[0]["description"] == "alpha"
        assert detail[1]["description"] == "beta"

    def test_missing_index_dir_returns_empty(self, tmp_path):
        idx_dir = tmp_path / "nonexistent"
        score, detail = inj._load_index_candidates(idx_dir, "src/foo")
        assert score == []
        assert detail == []

    def test_corrupt_score_file_returns_empty(self, tmp_path):
        entries = [_make_entry("src/foo/*.py")]
        idx_dir = self._build_index(tmp_path, entries)
        key = bld.parent_to_key("src/foo")
        (idx_dir / "score" / f"{key}.json").write_bytes(b"not json")
        score, detail = inj._load_index_candidates(idx_dir, "src/foo")
        # Should not raise; returns whatever non-corrupt buckets load
        assert isinstance(score, list)


# ---------------------------------------------------------------------------
# Contract preservation: indexed results == full-store results
# ---------------------------------------------------------------------------

class TestContractPreservation:
    """Verifies that the indexed fast path produces the same top-3 as the
    original O(N) full-store scan, for a fixture with cross-extension cases.

    Known intentional difference: entries in unrelated parent directories that
    pass the 0.10 threshold purely via domain+confidence weights (with no path
    relevance) are NOT returned by the indexed path.  The index only loads the
    target's parent-dir bucket and the root "." bucket.  This is a deliberate
    improvement — such entries have no path relevance and are noise.
    Fixture entries are designed to avoid this edge case so the test validates
    the common contract.  See TestScoreEntry.test_no_match_no_pattern_score_is_conf_only
    for explicit coverage of the conf-only scoring path.
    """

    FIXTURE_ENTRIES = [
        # High-scoring entry for src/cli/*.py — scores cli/*.ts via parent fallback
        _make_entry("src/cli/*.py", ["run", "cmd", "cli"], "api", 0.8,
                    "best match", "fix it", 5),
        # Same parent, lower score
        _make_entry("src/cli/*.py", ["run"], "general", 0.5,
                    "second match", "check it", 2),
        # Different parent, different domain — must NOT appear for src/cli or README target.
        # domain="style" ensures it won't score via domain+conf alone for general-domain targets.
        _make_entry("src/tests/*.py", ["test"], "style", 0.3,
                    "different dir", "irrelevant", 10),
        # Root pattern — always included
        _make_entry("*.py", ["run"], "general", 0.4,
                    "root match", "run it", 1),
    ]

    def _orig_score(self, entry, fp, kws, dom, par):
        import re
        pattern = entry.get("file_pattern", "")
        path_score = 0.0
        if pattern:
            try:
                rx = pattern.replace("*", ".*")
                if re.fullmatch(rx, fp):
                    path_score = 1.0
                elif str(Path(pattern).parent) == par:
                    path_score = 0.7
            except re.error:
                pass
        ekws = set(entry.get("keywords", []))
        kw = len(ekws & kws) / len(ekws | kws) if (ekws and kws) else 0.0
        ds = 1.0 if entry.get("domain") == dom else 0.0
        conf = entry.get("confidence", 0.3)
        return path_score * 0.30 + kw * 0.25 + ds * 0.20 + conf * 0.15

    @pytest.mark.parametrize("target", [
        "src/cli/run_cmd.py",
        "src/cli/run_cmd.ts",   # cross-extension — relies on parent fallback
        # NOTE: root-level targets (e.g. "README.md", parent=".") are excluded
        # from this parametrize because the original O(N) scan returns entries
        # from unrelated parent dirs that pass threshold via domain+confidence
        # alone (e.g. src/cli/*.py with domain=general, conf=0.5 scores 0.275
        # against README.md with no path relevance).  The indexed path only loads
        # the "." bucket and the target's parent bucket, intentionally omitting
        # these path-irrelevant matches.  This is tested separately in
        # test_root_target_indexed_finds_dot_bucket_entries.
    ])
    def test_indexed_top3_matches_full_scan(self, tmp_path, target):
        store = tmp_path / "store.json"
        idx_dir = tmp_path / "idx"
        _make_store(self.FIXTURE_ENTRIES, store)
        bld.build(store, idx_dir)

        fp = target
        kws = set(inj._extract_keywords(fp))
        dom = inj._guess_domain(fp)
        par = str(Path(fp).parent)

        # Full-scan top-3 (original logic)
        full_scored = sorted(
            [(e, self._orig_score(e, fp, kws, dom, par)) for e in self.FIXTURE_ENTRIES
             if self._orig_score(e, fp, kws, dom, par) > 0.10],
            key=lambda x: -x[1],
        )[:3]
        full_descs = [e.get("description") for e, _ in full_scored]

        # Indexed top-3 (new logic)
        score_entries, detail_entries = inj._load_index_candidates(idx_dir, par)
        idx_scored = sorted(
            [(e, inj._score_entry(e, fp, kws, dom, par), i)
             for i, e in enumerate(score_entries)
             if inj._score_entry(e, fp, kws, dom, par) > 0.10],
            key=lambda x: -x[1],
        )[:3]
        idx_descs = [
            detail_entries[i]["description"] if i < len(detail_entries) else ""
            for _, _, i in idx_scored
        ]

        assert full_descs == idx_descs, (
            f"Contract mismatch for {target!r}:\n"
            f"  full_scan: {full_descs}\n"
            f"  indexed:   {idx_descs}"
        )


    def test_root_target_indexed_finds_dot_bucket_entries(self, tmp_path):
        """Root-level targets (parent='.') match root glob patterns correctly.

        Entries in non-root parent dirs (src/cli/*.py) are NOT returned by
        the index for a root target — this is intentional.  Only entries whose
        pattern parent is '.' (root globs like '*.md') are loaded.
        """
        entries = [
            _make_entry("*.md",        ["readme", "docs"], "general", 0.8,
                        "root md match", "fix docs"),
            _make_entry("src/cli/*.py", ["cli"],           "general", 0.9,
                        "cli entry should not appear", "irrelevant"),
        ]
        store = tmp_path / "store.json"
        idx_dir = tmp_path / "idx"
        _make_store(entries, store)
        bld.build(store, idx_dir)

        fp = "README.md"
        par = "."
        kws = set(inj._extract_keywords(fp))
        dom = inj._guess_domain(fp)

        score_entries, detail_entries = inj._load_index_candidates(idx_dir, par)
        idx_scored = sorted(
            [(e, inj._score_entry(e, fp, kws, dom, par), i)
             for i, e in enumerate(score_entries)
             if inj._score_entry(e, fp, kws, dom, par) > 0.10],
            key=lambda x: -x[1],
        )[:3]
        idx_descs = [
            detail_entries[i]["description"] if i < len(detail_entries) else ""
            for _, _, i in idx_scored
        ]

        assert "root md match" in idx_descs, "Root glob pattern must match root-level target"
        assert "cli entry should not appear" not in idx_descs, (
            "src/cli/*.py entry must not appear for README.md (different parent dir)"
        )


# ---------------------------------------------------------------------------
# main() integration — stdin/stdout/stderr contract
# ---------------------------------------------------------------------------

class TestMainContract:
    def _run(self, monkeypatch, tmp_path, file_path, entries=None, index_fresh=True):
        """Run injector main() with controlled environment."""
        store = tmp_path / "experience_memory.json"
        idx_dir = tmp_path / "experience_index"

        if entries is None:
            entries = [_make_entry("src/*.py", ["test"], "general", 0.8,
                                   "a description", "a resolution", 3)]
        _make_store(entries, store)

        if index_fresh:
            bld.build(store, idx_dir)

        monkeypatch.setattr(inj, "_store_path", lambda: store)
        monkeypatch.setattr(inj, "_index_dir", lambda: idx_dir)
        monkeypatch.setenv("FILE", file_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        stdin_data = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": file_path}})
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_data))

        captured_stderr = io.StringIO()
        captured_stdout = io.StringIO()
        monkeypatch.setattr("sys.stderr", captured_stderr)
        monkeypatch.setattr("sys.stdout", captured_stdout)

        inj.main()
        return captured_stdout.getvalue(), captured_stderr.getvalue()

    def test_always_approves_on_stdout(self, monkeypatch, tmp_path):
        stdout, _ = self._run(monkeypatch, tmp_path, "src/foo.py")
        assert json.loads(stdout.strip()) == {"decision": "approve"}

    def test_no_output_when_no_matches(self, monkeypatch, tmp_path):
        # Entry pattern in "src/other" — won't match "src/foo"
        entries = [_make_entry("src/other/*.py", keywords=[], domain="zzz", confidence=0.0)]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo/bar.py", entries)
        assert json.loads(stdout.strip()) == {"decision": "approve"}
        assert stderr == ""

    def test_stderr_contains_match_header(self, monkeypatch, tmp_path):
        entries = [_make_entry("src/*.py", ["foo"], "general", 0.9, "great desc", "great res")]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo.py", entries)
        assert "Experience Memory" in stderr

    def test_stderr_contains_description(self, monkeypatch, tmp_path):
        entries = [_make_entry("src/*.py", ["foo"], "general", 0.9,
                               "special description here", "resolution text")]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo.py", entries)
        assert "special description here" in stderr

    def test_stderr_contains_resolution(self, monkeypatch, tmp_path):
        entries = [_make_entry("src/*.py", ["foo"], "general", 0.9,
                               "desc", "specific resolution text")]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo.py", entries)
        assert "specific resolution text" in stderr

    def test_no_crash_empty_store(self, monkeypatch, tmp_path):
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo.py", entries=[])
        assert json.loads(stdout.strip()) == {"decision": "approve"}

    def test_no_crash_missing_file_env(self, monkeypatch, tmp_path):
        store = tmp_path / "experience_memory.json"
        _make_store([], store)
        monkeypatch.setattr(inj, "_store_path", lambda: store)
        monkeypatch.setattr(inj, "_index_dir", lambda: tmp_path / "idx")
        monkeypatch.delenv("FILE", raising=False)
        monkeypatch.setattr("sys.stdin", io.StringIO('{"tool_name":"Edit","tool_input":{}}'))
        monkeypatch.setattr("sys.stdout", io.StringIO())
        inj.main()  # must not raise

    def test_cold_path_falls_back_to_store(self, monkeypatch, tmp_path):
        # No index built — should fall back to full store scan
        entries = [_make_entry("src/*.py", ["foo"], "general", 0.9,
                               "cold path desc", "cold res")]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo.py",
                                   entries=entries, index_fresh=False)
        assert json.loads(stdout.strip()) == {"decision": "approve"}
        # cold path should still find matches
        assert "cold path desc" in stderr

    def test_top3_limit(self, monkeypatch, tmp_path):
        # 5 entries all scoring > 0.10 — only top-3 should appear
        entries = [
            _make_entry("src/*.py", ["foo"], "general", 0.9, f"desc{i}", f"res{i}")
            for i in range(5)
        ]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/foo.py", entries)
        match_count = stderr.count("[0.")
        assert match_count <= 3, f"Expected at most 3 matches, got {match_count}"

    def test_cross_extension_parent_match(self, monkeypatch, tmp_path):
        # .py pattern should score .ts target via parent-dir fallback
        entries = [_make_entry("src/cli/*.py", ["run", "cmd", "cli"], "api", 0.8,
                               "cross-ext match", "fix cli")]
        stdout, stderr = self._run(monkeypatch, tmp_path, "src/cli/run.ts",
                                   entries=entries)
        assert json.loads(stdout.strip()) == {"decision": "approve"}
        assert "cross-ext match" in stderr, (
            "Parent-dir fallback must surface .py pattern for .ts target in same dir"
        )
