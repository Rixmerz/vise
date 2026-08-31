"""Two writers, one file, and a singleton that never looks at it again.

`get_experience_store()` loads once per process. The MCP server is a long-lived
process, so its in-memory list is a snapshot from whenever it booted — and
`save()` wrote that whole list back. Meanwhile `experience_recorder` runs as its
own interpreter on every commit and appends to the same file.

So any server-side `save()` was a total overwrite of everything the hooks had
recorded since the server started. Not a partial loss and not a torn file: a
clean, atomic, unrecoverable replacement, which is the shape that leaves no
evidence.

The read path made it worse. `experience_query` is annotated `readOnlyHint` and
saved on its way out, to persist a recall bump — so *reading* the store could
erase it.
"""
from __future__ import annotations

import json

import pytest

from vise.engines import experience_memory
from vise.engines.experience_memory import (
    MAX_ENTRIES,
    ExperienceEntry,
    ExperienceMemoryStore,
)


@pytest.fixture(autouse=True)
def _fresh_singleton():
    experience_memory._experience_store = None
    yield
    experience_memory._experience_store = None


def _entry(desc: str, **kw) -> ExperienceEntry:
    base = dict(type="fix", file_pattern="*.py", domain="backend", description=desc)
    base.update(kw)
    return ExperienceEntry(**base)


def _store_at(path) -> ExperienceMemoryStore:
    store = ExperienceMemoryStore()
    store._file_path = path
    store._scope = "global"
    store.entries = []
    return store


def _write_raw(path, descriptions):
    """Stand in for the hook: append straight to the file, as it does."""
    existing = json.loads(path.read_text())["entries"] if path.exists() else []
    for i, desc in enumerate(descriptions):
        existing.append({
            "type": "fix", "file_pattern": f"*.hook{i}", "domain": "hooks",
            "description": desc, "confidence": 0.5, "occurrences": 1,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": existing, "version": "1.0"}, indent=2))


def test_a_server_side_save_does_not_erase_what_a_hook_wrote(tmp_path):
    """The lost update, in the smallest form that shows it."""
    path = tmp_path / "experience.json"
    server = _store_at(path)
    server.record(_entry("the server knew this at boot"))
    server.save()

    # The hook runs in its own process and appends while the server holds its
    # snapshot from before.
    _write_raw(path, ["the hook recorded this after the server booted"])

    server.record(_entry("something the server learned later"))
    server.save()

    on_disk = [e["description"] for e in json.loads(path.read_text())["entries"]]
    assert "the hook recorded this after the server booted" in on_disk, (
        f"the hook's entry was erased by a server-side save: {on_disk}"
    )
    assert "something the server learned later" in on_disk


def test_a_read_only_query_never_writes_the_store(tmp_path, monkeypatch):
    """`experience_query` is annotated readOnlyHint. It has to mean it."""
    path = tmp_path / "experience.json"
    store = _store_at(path)
    store.record(_entry("seeded"))
    store.save()
    before = path.read_text()

    monkeypatch.setattr(experience_memory, "_experience_store", store)
    from vise.tools import experience as experience_tools

    fn = _registered(experience_tools, "experience_query")
    result = fn(file_path="src/app.py", top_n=5, min_score=0.0,
                scope="global", project_dir=str(tmp_path))
    assert result["matches"] > 0, (
        "this test only means something if the query actually matched — the "
        f"save path is gated on that: {result}"
    )

    assert path.read_text() == before, "a read-only tool rewrote the store"


def _registered(module, name):
    """Pull one registered MCP tool function out of its register() closure."""
    import inspect

    src = inspect.getsource(module)
    assert f"def {name}(" in src, f"{name} is not defined in {module.__name__}"

    class _Recorder:
        def __init__(self):
            self.fns = {}

        def tool(self, *a, **kw):
            def deco(fn):
                self.fns[fn.__name__] = fn
                return fn
            return deco

    rec = _Recorder()
    for attr in dir(module):
        obj = getattr(module, attr)
        if callable(obj) and attr.startswith("register"):
            try:
                obj(rec)
            except Exception:
                continue
    assert name in rec.fns, f"{name} was not registered (got {sorted(rec.fns)})"
    return rec.fns[name]


def test_merging_keeps_the_cap(tmp_path):
    """Merging two writers must not let the store grow past the engine's cap."""
    path = tmp_path / "experience.json"
    _write_raw(path, [f"hook entry {i}" for i in range(MAX_ENTRIES + 50)])

    server = _store_at(path)
    server.record(_entry("one more from the server"))
    server.save()

    assert len(json.loads(path.read_text())["entries"]) <= MAX_ENTRIES


def test_every_persisted_entry_carries_an_id(tmp_path):
    """The hook writes no `id`. Merged entries still have to be addressable."""
    path = tmp_path / "experience.json"
    _write_raw(path, ["written by the hook, with no id field"])

    server = _store_at(path)
    server.record(_entry("from the server"))
    server.save()

    ids = [e.get("id") for e in json.loads(path.read_text())["entries"]]
    assert all(ids), f"entries persisted without an id: {ids}"
    assert len(set(ids)) == len(ids), "ids collide"


def test_a_store_with_no_file_on_disk_still_saves(tmp_path):
    """The guard: merging must not need the file to already exist."""
    path = tmp_path / "nested" / "experience.json"
    store = _store_at(path)
    store.record(_entry("first ever"))
    store.save()

    assert len(json.loads(path.read_text())["entries"]) == 1


def test_an_unreadable_store_does_not_lose_what_is_in_memory(tmp_path):
    """Merging against garbage must keep this process's own entries."""
    path = tmp_path / "experience.json"
    path.write_text("{not json")

    store = _store_at(path)
    store.record(_entry("in memory and worth keeping"))
    store.save()

    on_disk = [e["description"] for e in json.loads(path.read_text())["entries"]]
    assert "in memory and worth keeping" in on_disk
