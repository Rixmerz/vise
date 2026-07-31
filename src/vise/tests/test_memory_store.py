"""Tests for vise.engines.memory_store: the ~/.vise/memory/ frontmatter store.

Covers:
1. save()/load_all() round-trips every field intact.
2. Malformed frontmatter (no closing delimiter) does not crash load_all —
   the caller falls back to filename-derived defaults.
3. query() always surfaces priority:high nodes regardless of tag score.
4. query() orders remaining nodes by score() descending.
5. query() expands one hop of links, even for a zero-score linked node.
6. query() records a recall event (bumps stability, sets last_reviewed) and
   persists it to disk.
7. is_expired() respects ttl (d/w/h units) against mtime.
8. stats() reports total/active/expired counts and estimated_tokens.

MEMORY_DIR is resolved at import time from Path.home() and is NOT touched
by the suite-wide XDG conftest fixture (that fixture only patches
XDG_DATA_HOME + experience_memory's module constants). Every test here
monkeypatches memory_store.MEMORY_DIR directly to a tmp_path so nothing
ever touches the real ~/.vise/memory/.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vise.engines import memory_store
from vise.engines.memory_store import MemoryNode


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect MEMORY_DIR to a tmpdir; never touch the real ~/.vise/memory/."""
    isolated = tmp_path / "vise-memory"
    monkeypatch.setattr(memory_store, "MEMORY_DIR", isolated)
    return isolated


def test_save_then_load_all_roundtrips_every_field() -> None:
    """A node with every field populated survives a save/load_all cycle intact."""
    node = MemoryNode(
        id="my-note",
        name="My Note",
        description="a short summary",
        type="project",
        tags=["alpha", "beta"],
        links=["other-note"],
        priority="high",
        ttl="30d",
        body="the body text",
    )

    memory_store.save(node)
    loaded = memory_store.load_all()

    assert "my-note" in loaded
    reloaded = loaded["my-note"]
    assert reloaded.name == "My Note"
    assert reloaded.description == "a short summary"
    assert reloaded.type == "project"
    assert reloaded.tags == ["alpha", "beta"]
    assert reloaded.links == ["other-note"]
    assert reloaded.priority == "high"
    assert reloaded.ttl == "30d"
    assert reloaded.body.strip() == "the body text"


def test_load_all_with_malformed_frontmatter_does_not_crash() -> None:
    """A .md file with no closing '---' must not raise — load_all falls back
    to filename-derived defaults instead of propagating a parse error."""
    memory_store._ensure_dir()
    broken = memory_store.MEMORY_DIR / "broken-note.md"
    broken.write_text("---\nname: Unterminated\nthis has no closing delimiter at all")

    loaded = memory_store.load_all()

    assert "broken-note" in loaded
    node = loaded["broken-note"]
    assert node.type == "reference"  # default when frontmatter unparsed
    assert node.tags == []


def test_score_zero_overlap_never_scores_positive() -> None:
    """A node with zero tag overlap against the query must not score above
    zero, even though FSRS retrievability alone is always positive.

    Regression guard: score() used to add fsrs_r * 0.1 unconditionally
    whenever query_tags was non-empty, so every stored node came back with
    a small positive score regardless of topical relevance — collapsing
    ranking to rely on top_n truncation alone. The FSRS term must modulate
    an existing topical match, not manufacture one.
    """
    node = MemoryNode(id="unrelated", name="Unrelated", description="",
                       type="reference", tags=["rust", "cli"], priority="normal")

    assert node.score(["docker", "kubernetes"]) <= 0.0


def test_query_excludes_nodes_with_zero_tag_overlap() -> None:
    """query() must not return nodes whose tags share nothing with the
    query — the actual user-visible symptom of the FSRS-baseline bug.

    Regression guard: before the fix, every normal-priority node scored a
    small positive baseline (fsrs_r * 0.1) regardless of tag overlap, so
    query() returned every stored node up to top_n instead of only the
    topically relevant one.
    """
    matching = MemoryNode(id="matching", name="Matching", description="",
                           type="reference", tags=["docker"], priority="normal")
    disjoint = [
        MemoryNode(id=f"disjoint{i}", name=f"Disjoint{i}", description="",
                   type="reference", tags=[f"unrelated{i}"], priority="normal")
        for i in range(3)
    ]
    memory_store.save(matching)
    for n in disjoint:
        memory_store.save(n)

    result = memory_store.query(tags=["docker"], top_n=5, expand_links=False)

    assert {n.id for n in result} == {"matching"}


def test_query_always_includes_high_priority_node_regardless_of_score() -> None:
    """A priority:high node with zero tag overlap must still be returned."""
    high = MemoryNode(id="h1", name="High", description="", type="reference",
                       tags=["unrelated"], priority="high")
    normal = MemoryNode(id="n1", name="Normal", description="", type="reference",
                         tags=["python"], priority="normal")
    memory_store.save(high)
    memory_store.save(normal)

    result = memory_store.query(tags=["python"], top_n=5, expand_links=False)

    result_ids = {n.id for n in result}
    assert "h1" in result_ids, "high-priority node was dropped despite zero overlap"


def test_query_orders_remaining_nodes_by_score_descending() -> None:
    """Non-high-priority nodes must come back sorted by score(), best first."""
    strong = MemoryNode(id="strong", name="Strong", description="", type="reference",
                         tags=["python", "testing"], priority="normal")
    weak = MemoryNode(id="weak", name="Weak", description="", type="reference",
                       tags=["python", "unrelated1", "unrelated2"], priority="normal")
    memory_store.save(strong)
    memory_store.save(weak)

    result = memory_store.query(tags=["python", "testing"], top_n=5, expand_links=False)

    result_ids = [n.id for n in result]
    assert result_ids.index("strong") < result_ids.index("weak"), (
        f"expected 'strong' (higher tag overlap ratio) ranked before 'weak', got {result_ids}"
    )


def test_query_expands_one_hop_of_links_beyond_top_n_cutoff() -> None:
    """A node linked from a matched result is pulled in even after it has
    already been crowded out of the top_n-ranked scored list.

    top_n is capped at 1 so only the best-scoring "seed" survives the cut;
    several higher-scoring filler nodes crowd "linked" (zero tag overlap,
    score 0.0 — see test_score_zero_overlap_never_scores_positive) out of
    the ranked list entirely — it can only re-appear via the one-hop link
    expansion.
    """
    seed = MemoryNode(id="seed", name="Seed", description="", type="reference",
                       tags=["python", "testing"], links=["linked"], priority="normal")
    # Fillers carry an extra unmatched tag so their overlap RATIO (and thus
    # score) is strictly lower than seed's — this makes "seed" the
    # deterministic rank-1 pick regardless of dict/glob iteration order.
    fillers = [
        MemoryNode(id=f"filler{i}", name=f"Filler{i}", description="", type="reference",
                   tags=["python", "testing", "noise"], priority="normal")
        for i in range(3)
    ]
    linked = MemoryNode(id="linked", name="Linked", description="", type="reference",
                        tags=[], priority="normal")
    memory_store.save(seed)
    for f in fillers:
        memory_store.save(f)
    memory_store.save(linked)

    result = memory_store.query(tags=["python", "testing"], top_n=1, expand_links=True)

    result_ids = {n.id for n in result}
    assert "linked" in result_ids, "linked node was not pulled in via one-hop expansion"


def test_query_does_not_expand_links_when_disabled() -> None:
    """Same crowded-out setup as above, but expand_links=False must leave
    the linked node out entirely."""
    seed = MemoryNode(id="seed2", name="Seed2", description="", type="reference",
                       tags=["python", "testing"], links=["linked2"], priority="normal")
    # Extra unmatched "noise" tag keeps fillers' score strictly below seed2's,
    # making seed2 the deterministic rank-1 pick (see note in the sibling
    # expand-links test above).
    fillers = [
        MemoryNode(id=f"filler2-{i}", name=f"Filler2-{i}", description="", type="reference",
                   tags=["python", "testing", "noise"], priority="normal")
        for i in range(3)
    ]
    linked = MemoryNode(id="linked2", name="Linked2", description="", type="reference",
                        tags=[], priority="normal")
    memory_store.save(seed)
    for f in fillers:
        memory_store.save(f)
    memory_store.save(linked)

    result = memory_store.query(tags=["python", "testing"], top_n=1, expand_links=False)

    result_ids = {n.id for n in result}
    assert "linked2" not in result_ids


def test_query_records_recall_event_and_persists_it() -> None:
    """Returned nodes get last_reviewed bumped to now and stability
    increased, and that update is written back to disk."""
    old_ts = (datetime.now() - timedelta(days=5)).isoformat()
    node = MemoryNode(id="recall-me", name="Recall", description="", type="reference",
                       tags=["python"], priority="normal", stability=10.0,
                       last_reviewed=old_ts)
    memory_store.save(node)

    memory_store.query(tags=["python"], top_n=5, expand_links=False)

    reloaded = memory_store.load_all()["recall-me"]
    assert reloaded.stability > 10.0, "stability must increase after being recalled"
    reloaded_dt = datetime.fromisoformat(reloaded.last_reviewed)
    assert (datetime.now() - reloaded_dt).total_seconds() < 5, (
        "last_reviewed must be bumped to ~now on recall"
    )


def test_is_expired_true_past_ttl() -> None:
    """A node whose mtime + ttl is in the past reports expired."""
    node = MemoryNode(id="old", name="Old", description="", type="reference", ttl="1d")
    node.mtime = (datetime.now() - timedelta(days=5)).timestamp()

    assert node.is_expired() is True


def test_is_expired_false_within_ttl() -> None:
    """A node whose mtime is well within the ttl window is not expired."""
    node = MemoryNode(id="fresh", name="Fresh", description="", type="reference", ttl="30d")
    node.mtime = (datetime.now() - timedelta(days=1)).timestamp()

    assert node.is_expired() is False


def test_is_expired_false_without_ttl() -> None:
    """A node with no ttl set is never expired."""
    node = MemoryNode(id="no-ttl", name="NoTTL", description="", type="reference", ttl="")

    assert node.is_expired() is False


def test_stats_reports_total_active_expired_counts() -> None:
    """stats() must count expired nodes separately from active ones.

    load_all() derives mtime from the file's actual filesystem mtime (not
    the frontmatter), so backdating requires os.utime on the saved file
    rather than setting node.mtime before save().
    """
    import os

    active = MemoryNode(id="active-node", name="Active", description="d", type="reference")
    expired = MemoryNode(id="expired-node", name="Expired", description="d", type="reference",
                          ttl="1d")
    memory_store.save(active)
    memory_store.save(expired)
    old_time = (datetime.now() - timedelta(days=10)).timestamp()
    expired_path = memory_store.MEMORY_DIR / "expired-node.md"
    os.utime(expired_path, (old_time, old_time))

    result = memory_store.stats()

    assert result["total"] == 2
    assert result["active"] == 1
    assert result["expired"] == 1
