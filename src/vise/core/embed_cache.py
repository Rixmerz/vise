"""SQLite-backed embedding cache for tool descriptions.

Schema (versioned per model slug):

    tools(
        mcp_name  TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        text_hash TEXT NOT NULL,
        description TEXT,
        input_schema TEXT,
        embedding BLOB NOT NULL,      -- float32 little-endian
        updated_at REAL NOT NULL,
        PRIMARY KEY (mcp_name, tool_name)
    )

One DB per model slug so switching models never mixes dimensions. DB path:

    ~/.local/share/vise/tools_<model_slug>.db

Search is cache-driven — it never requires a live MCP connection. Writers
populate on registration and refresh when descriptions change.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from vise.core import paths
from vise.core.embeddings import get_embedder, model_slug

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class ToolRecord:
    mcp_name: str
    tool_name: str
    description: str
    input_schema: dict[str, object]
    embedding: list[float]
    updated_at: float


def _db_path(slug: str | None = None) -> Path:
    s = slug or model_slug()
    return paths.ensure(paths.data_dir()) / f"tools_{s}.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (
    mcp_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    description TEXT NOT NULL,
    input_schema TEXT NOT NULL,
    embedding BLOB NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (mcp_name, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_tools_mcp ON tools(mcp_name);
"""


@contextmanager
def _open(slug: str | None = None) -> "Iterator[sqlite3.Connection]":
    path = _db_path(slug)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript(_SCHEMA)
        yield conn
    finally:
        conn.close()


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _text_key(description: str, input_schema: dict[str, object]) -> str:
    combined = description + "\n" + json.dumps(input_schema, sort_keys=True, default=str)
    return hashlib.sha1(combined.encode("utf-8"), usedforsecurity=False).hexdigest()


def _format_for_embedding(tool_name: str, description: str, input_schema: dict[str, object]) -> str:
    """Construct the text blob that gets embedded.

    Includes tool name, description, and a condensed schema view so queries like
    "tool that takes a file path and returns AST" match on signature shape.
    """
    props = []
    if isinstance(input_schema, dict):
        schema_props = input_schema.get("properties")
        if isinstance(schema_props, dict):
            for name, spec in schema_props.items():
                hint = ""
                if isinstance(spec, dict):
                    t = spec.get("type")
                    d = spec.get("description", "")
                    if t:
                        hint = f": {t}"
                    if d:
                        hint += f" — {d}"
                props.append(f"{name}{hint}")
    params = ", ".join(props) if props else "(no params)"
    return f"{tool_name}: {description}\nParams: {params}"


def upsert_tools(
    mcp_name: str,
    tools: "list[dict[str, object]]",
    *,
    slug: str | None = None,
) -> tuple[int, str | None]:
    """Embed and write tool records. Returns (count, error_msg).

    count is the number of rows written; error_msg is None on success or a
    human-readable string describing the failure on the silent-zero paths.

    Skips tools whose ``(mcp_name, tool_name, text_hash)`` already exists
    in the cache — their description + schema haven't changed since the
    last embed. This keeps repeat invocations cheap; on the hot path
    ``_tool_archive.archive_all`` runs every server startup, and without
    this dedup the MCP handshake would pay the full ~40s embedding cost
    even when nothing has changed since last launch.
    """
    now = time.time()
    all_metas: list[tuple[str, str, str, dict[str, object], str]] = []
    for t in tools:
        name = str(t.get("name") or t.get("tool_name") or "")
        if not name:
            continue
        desc = str(t.get("description") or "")
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        all_metas.append((mcp_name, name, _text_key(desc, schema), schema, desc))

    if not all_metas:
        return 0, None

    # Filter out rows already present with matching text_hash.
    with _open(slug) as conn:
        rows = conn.execute(
            "SELECT tool_name, text_hash FROM tools WHERE mcp_name = ?",
            (mcp_name,),
        ).fetchall()
    existing = {row[0]: row[1] for row in rows}
    fresh = [m for m in all_metas if existing.get(m[1]) != m[2]]

    if not fresh:
        return 0, None

    emb = get_embedder()
    if not emb.available:
        last_err = getattr(emb, "_load_error", None) or getattr(emb, "last_error", None)
        return 0, f"embedder unavailable: {last_err or 'model load failed'}"

    payloads: list[tuple[str, str, str, str, str, bytes, float]] = []
    metas = fresh
    texts = [_format_for_embedding(name, desc, schema) for (_, name, _th, schema, desc) in metas]

    vecs = emb.embed_many(texts)
    if vecs is None:
        return 0, "embed_many returned None"

    for (mcp, tool_name, th, schema, desc), vec in zip(metas, vecs, strict=True):
        payloads.append(
            (
                mcp,
                tool_name,
                th,
                desc,
                json.dumps(schema, default=str),
                _pack(vec),
                now,
            )
        )

    with _open(slug) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO tools
            (mcp_name, tool_name, text_hash, description, input_schema, embedding, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
    return len(payloads), None


def list_tools(
    mcp_name: str | None = None,
    *,
    slug: str | None = None,
) -> list[ToolRecord]:
    q = "SELECT mcp_name, tool_name, description, input_schema, embedding, updated_at FROM tools"
    params: tuple[object, ...] = ()
    if mcp_name:
        q += " WHERE mcp_name = ?"
        params = (mcp_name,)
    with _open(slug) as conn:
        rows = conn.execute(q, params).fetchall()
    out: list[ToolRecord] = []
    for mcp, tool, desc, schema_json, emb_blob, updated in rows:
        try:
            schema = json.loads(schema_json) if schema_json else {}
        except json.JSONDecodeError:
            schema = {}
        out.append(
            ToolRecord(
                mcp_name=mcp,
                tool_name=tool,
                description=desc,
                input_schema=schema,
                embedding=_unpack(emb_blob),
                updated_at=updated,
            )
        )
    return out


def remove_mcp(mcp_name: str, *, slug: str | None = None) -> int:
    with _open(slug) as conn:
        cur = conn.execute("DELETE FROM tools WHERE mcp_name = ?", (mcp_name,))
        return cur.rowcount or 0


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search(
    query: str,
    *,
    top_k: int = 10,
    mcp_name: str | None = None,
    slug: str | None = None,
) -> list[tuple[ToolRecord, float]]:
    """Semantic search. Returns (tool, score) pairs ordered by score desc."""
    emb = get_embedder()
    qvec = emb.embed_one(query) if emb.available else None
    records = list_tools(mcp_name=mcp_name, slug=slug)
    if not records:
        return []

    if qvec is None:
        # Fallback: keyword match on description + tool_name
        ql = query.lower()
        scored = [
            (r, float(ql in r.description.lower() or ql in r.tool_name.lower()))
            for r in records
        ]
        scored = [(r, s) for r, s in scored if s > 0]
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[:top_k]

    scored = [(r, _cosine(qvec, r.embedding)) for r in records]
    scored.sort(key=lambda p: p[1], reverse=True)
    return scored[:top_k]


__all__ = [
    "ToolRecord",
    "list_tools",
    "remove_mcp",
    "search",
    "upsert_tools",
]
