"""Tests for vise.core.embed_cache — SQLite cache lifecycle and search.

Covers:
- Database is created/opened on first use (schema applied)
- put/get roundtrip via upsert_tools + list_tools
- Hash-based dedup: identical tool is skipped on second upsert
- New hash on changed description → row is refreshed
- Keyword-fallback search when embedder is unavailable
- remove_mcp deletes only the targeted mcp rows
- list_tools can filter by mcp_name
- Corrupt/missing DB entry handled gracefully in list_tools
- _pack/_unpack roundtrip preserves float values
- _text_key is stable (same inputs → same hash)
- _format_for_embedding includes tool name and description
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from vise.core.embed_cache import (
    _pack,
    _unpack,
    _text_key,
    _format_for_embedding,
    _cosine,
    list_tools,
    remove_mcp,
    search,
    upsert_tools,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_tool(name: str = "my_tool", desc: str = "A test tool", schema: dict | None = None) -> dict:
    return {
        "name": name,
        "description": desc,
        "inputSchema": schema or {"properties": {"path": {"type": "string"}}},
    }


def _stub_embedder(dim: int = 4) -> MagicMock:
    """Return a mock embedder that produces deterministic unit vectors."""
    emb = MagicMock()
    emb.available = True
    # embed_one returns a list of dim floats
    emb.embed_one.return_value = [1.0 / dim] * dim
    # embed_many mirrors embed_one for each input
    emb.embed_many.side_effect = lambda texts: [[1.0 / dim] * dim for _ in texts]
    return emb


def _unavailable_embedder() -> MagicMock:
    emb = MagicMock()
    emb.available = False
    emb.embed_one.return_value = None
    emb.embed_many.return_value = None
    return emb


@pytest.fixture()
def db_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Redirect data_dir to tmp_path so DB files land in the sandbox."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    return "testslug"


# ---------------------------------------------------------------------------
# _pack / _unpack
# ---------------------------------------------------------------------------

def test_pack_unpack_roundtrip_exact():
    original = [0.1, 0.5, -0.3, 1.0]
    blob = _pack(original)
    recovered = _unpack(blob)
    # struct.pack f is float32; tolerate float32 rounding
    for a, b in zip(original, recovered):
        assert abs(a - b) < 1e-6, f"roundtrip mismatch: {a} vs {b}"


def test_pack_produces_correct_byte_length():
    vec = [1.0, 2.0, 3.0]
    assert len(_pack(vec)) == len(vec) * 4  # 4 bytes per float32


def test_unpack_empty_blob_returns_empty_list():
    assert _unpack(b"") == []


# ---------------------------------------------------------------------------
# _text_key
# ---------------------------------------------------------------------------

def test_text_key_stable_for_same_inputs():
    h1 = _text_key("A description", {"a": 1})
    h2 = _text_key("A description", {"a": 1})
    assert h1 == h2


def test_text_key_differs_for_different_description():
    h1 = _text_key("desc A", {})
    h2 = _text_key("desc B", {})
    assert h1 != h2


def test_text_key_differs_for_different_schema():
    h1 = _text_key("same", {"x": 1})
    h2 = _text_key("same", {"x": 2})
    assert h1 != h2


def test_text_key_is_12_or_40_hex_chars():
    h = _text_key("hello", {})
    assert len(h) == 40  # sha1 hex digest
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# _format_for_embedding
# ---------------------------------------------------------------------------

def test_format_for_embedding_includes_tool_name():
    text = _format_for_embedding("search_files", "Find files by pattern", {})
    assert "search_files" in text


def test_format_for_embedding_includes_description():
    text = _format_for_embedding("t", "A detailed description here", {})
    assert "A detailed description here" in text


def test_format_for_embedding_includes_param_names():
    schema = {"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}
    text = _format_for_embedding("search", "desc", schema)
    assert "query" in text
    assert "limit" in text


def test_format_for_embedding_no_params_placeholder():
    text = _format_for_embedding("noop", "does nothing", {})
    assert "(no params)" in text


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------

def test_cosine_identical_vectors_returns_one():
    v = [0.5, 0.5, 0.5, 0.5]
    assert abs(_cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors_returns_zero():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(_cosine(a, b)) < 1e-6


def test_cosine_zero_vector_returns_zero():
    a = [0.0, 0.0]
    b = [1.0, 0.0]
    assert _cosine(a, b) == 0.0


# ---------------------------------------------------------------------------
# upsert_tools + list_tools (put/get roundtrip)
# ---------------------------------------------------------------------------

def test_upsert_then_list_returns_the_inserted_tool(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        count, err = upsert_tools("my_mcp", [_make_tool("tool_a", "Does something")], slug=db_slug)

    assert err is None
    assert count == 1
    records = list_tools(slug=db_slug)
    assert len(records) == 1
    r = records[0]
    assert r.mcp_name == "my_mcp"
    assert r.tool_name == "tool_a"
    assert r.description == "Does something"
    assert len(r.embedding) > 0


def test_upsert_multiple_tools_all_inserted(db_slug):
    stub = _stub_embedder()
    tools = [_make_tool(f"tool_{i}", f"desc {i}") for i in range(3)]
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        count, err = upsert_tools("mcp1", tools, slug=db_slug)

    assert err is None
    assert count == 3
    assert len(list_tools(slug=db_slug)) == 3


def test_upsert_same_tool_twice_skips_second_call(db_slug):
    stub = _stub_embedder()
    tool = _make_tool("stable_tool", "same description")
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        count1, _ = upsert_tools("mcp", [tool], slug=db_slug)
        count2, _ = upsert_tools("mcp", [tool], slug=db_slug)

    assert count1 == 1
    assert count2 == 0  # no change → skipped


def test_upsert_changed_description_refreshes_row(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", [_make_tool("t", "original desc")], slug=db_slug)
        count, err = upsert_tools("mcp", [_make_tool("t", "UPDATED desc")], slug=db_slug)

    assert err is None
    assert count == 1
    records = list_tools(mcp_name="mcp", slug=db_slug)
    assert records[0].description == "UPDATED desc"


def test_upsert_with_unavailable_embedder_returns_error(db_slug):
    stub = _unavailable_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        count, err = upsert_tools("mcp", [_make_tool()], slug=db_slug)

    assert count == 0
    assert err is not None
    assert "unavailable" in err


def test_upsert_empty_tools_list_returns_zero(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        count, err = upsert_tools("mcp", [], slug=db_slug)
    assert count == 0
    assert err is None


def test_upsert_skips_tool_missing_name(db_slug):
    stub = _stub_embedder()
    bad_tool = {"description": "no name field"}
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        count, err = upsert_tools("mcp", [bad_tool], slug=db_slug)
    assert count == 0


# ---------------------------------------------------------------------------
# list_tools filtering
# ---------------------------------------------------------------------------

def test_list_tools_filter_by_mcp_name_returns_subset(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("alpha", [_make_tool("a1")], slug=db_slug)
        upsert_tools("beta",  [_make_tool("b1"), _make_tool("b2")], slug=db_slug)

    alpha_records = list_tools(mcp_name="alpha", slug=db_slug)
    beta_records  = list_tools(mcp_name="beta",  slug=db_slug)
    all_records   = list_tools(slug=db_slug)

    assert len(alpha_records) == 1
    assert len(beta_records) == 2
    assert len(all_records) == 3
    assert all(r.mcp_name == "alpha" for r in alpha_records)


def test_list_tools_returns_empty_for_unknown_mcp(db_slug):
    assert list_tools(mcp_name="nobody", slug=db_slug) == []


# ---------------------------------------------------------------------------
# remove_mcp
# ---------------------------------------------------------------------------

def test_remove_mcp_deletes_all_rows_for_that_mcp(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("kill_me", [_make_tool("t1"), _make_tool("t2")], slug=db_slug)
        upsert_tools("keep_me", [_make_tool("t3")], slug=db_slug)

    deleted = remove_mcp("kill_me", slug=db_slug)
    assert deleted == 2
    assert list_tools(mcp_name="kill_me", slug=db_slug) == []
    assert len(list_tools(mcp_name="keep_me", slug=db_slug)) == 1


def test_remove_mcp_nonexistent_returns_zero(db_slug):
    result = remove_mcp("ghost", slug=db_slug)
    assert result == 0


# ---------------------------------------------------------------------------
# search — keyword fallback (no real embedder needed)
# ---------------------------------------------------------------------------

def test_search_keyword_fallback_matches_description(db_slug):
    """When embedder is unavailable, keyword search on description must work."""
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", [
            _make_tool("find_files", "Find files on the filesystem"),
            _make_tool("send_email", "Send an email message"),
        ], slug=db_slug)

    unavail = _unavailable_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=unavail):
        results = search("filesystem", slug=db_slug)

    assert len(results) >= 1
    top_record, top_score = results[0]
    assert top_record.tool_name == "find_files"
    assert top_score > 0


def test_search_keyword_fallback_matches_tool_name(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", [_make_tool("unique_xyz_tool", "generic desc")], slug=db_slug)

    unavail = _unavailable_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=unavail):
        results = search("unique_xyz_tool", slug=db_slug)

    assert len(results) == 1


def test_search_keyword_no_match_returns_empty(db_slug):
    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", [_make_tool("tool_a", "does alpha things")], slug=db_slug)

    unavail = _unavailable_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=unavail):
        results = search("zzz_no_match_xyz", slug=db_slug)

    assert results == []


def test_search_returns_empty_when_cache_is_empty(db_slug):
    unavail = _unavailable_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=unavail):
        results = search("anything", slug=db_slug)
    assert results == []


def test_search_top_k_limits_results(db_slug):
    stub = _stub_embedder()
    tools = [_make_tool(f"tool_{i}", f"keyword hit {i}") for i in range(5)]
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", tools, slug=db_slug)

    unavail = _unavailable_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=unavail):
        results = search("keyword", top_k=3, slug=db_slug)

    assert len(results) <= 3


def test_search_semantic_returns_results_ordered_by_score(db_slug):
    """With a real embedder stub, cosine scores are computed and sorted descending."""
    stub = MagicMock()
    stub.available = True
    # embed_many produces [1,0,0,0] for all tools
    stub.embed_many.return_value = [[1.0, 0.0, 0.0, 0.0]] * 2
    # embed_one for query produces a different vector
    stub.embed_one.return_value = [0.9, 0.1, 0.0, 0.0]

    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", [_make_tool("a", "x"), _make_tool("b", "y")], slug=db_slug)
        results = search("query text", slug=db_slug)

    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Corrupt DB entry
# ---------------------------------------------------------------------------

def test_list_tools_handles_corrupt_schema_json(db_slug):
    """A row with malformed JSON in input_schema must not crash list_tools."""
    import sqlite3
    from vise.core.embed_cache import _db_path

    stub = _stub_embedder()
    with patch("vise.core.embed_cache.get_embedder", return_value=stub):
        upsert_tools("mcp", [_make_tool("good_tool")], slug=db_slug)

    # Manually corrupt the schema column for the row
    db_path = _db_path(db_slug)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE tools SET input_schema = ? WHERE tool_name = ?",
        ("{not: valid json!!!", "good_tool"),
    )
    conn.commit()
    conn.close()

    # Should not raise; corrupt schema → empty dict
    records = list_tools(slug=db_slug)
    assert len(records) == 1
    assert records[0].input_schema == {}
