"""Regression tests for three fixes:

1. ``project_memories/`` basename collisions (mirrors the ``states/`` fix).
2. Dead ``_deploy_agents_inline`` seam in ``goal_bootstrap`` no longer claims
   agents were deployed.
3. ``experience_stats``' ``oldest`` field is never ``""``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestProjectMemoryCollision:
    def test_two_projects_same_basename_diverge_once_claimed(self, tmp_path: Path) -> None:
        from vise.core import state_paths
        from vise.hooks import _xdg

        proj_a = tmp_path / "a" / "myrepo"
        proj_b = tmp_path / "b" / "myrepo"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)

        # proj_a claims the plain "myrepo" state dir first (real flow: any
        # state_dir()/graph_state_path() call does this).
        state_paths.state_dir(str(proj_a))

        mem_a = _xdg.project_memory_path(str(proj_a))
        mem_b = _xdg.project_memory_path(str(proj_b))

        assert mem_a != mem_b, "two different projects sharing a basename collided"

    def test_preexisting_project_memories_dir_still_found(self, tmp_path: Path) -> None:
        from vise.hooks import _xdg

        proj = tmp_path / "myrepo"
        proj.mkdir()

        # Simulate a pre-existing legacy store at the plain-basename location
        # (no marker claimed yet — the common, non-colliding case).
        legacy_path = _xdg.data_dir() / "project_memories" / "myrepo" / "experience_memory.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({"entries": [{"id": "x"}]}))

        resolved = _xdg.project_memory_path(str(proj))
        assert resolved == legacy_path
        assert json.loads(resolved.read_text())["entries"][0]["id"] == "x"

    def test_memory_key_agrees_with_state_key(self, tmp_path: Path) -> None:
        from vise.hooks import _xdg

        proj = tmp_path / "myrepo"
        proj.mkdir()

        state_key = _xdg.project_state_dir(str(proj)).name
        memory_key = _xdg.project_memory_dir(str(proj)).name
        assert state_key == memory_key

    def test_experience_memory_store_uses_collision_proof_key(self, tmp_path: Path) -> None:
        """ExperienceMemoryStore.load(project_dir=...) must resolve the same
        directory as _xdg.project_memory_path for the same project dir."""
        from vise.core import state_paths
        from vise.engines.experience_memory import ExperienceMemoryStore
        from vise.hooks import _xdg

        proj_a = tmp_path / "a" / "myrepo"
        proj_b = tmp_path / "b" / "myrepo"
        proj_a.mkdir(parents=True)
        proj_b.mkdir(parents=True)
        state_paths.state_dir(str(proj_a))

        store_a = ExperienceMemoryStore()
        store_a.load(scope="project", project_name="myrepo", project_dir=str(proj_a))
        store_b = ExperienceMemoryStore()
        store_b.load(scope="project", project_name="myrepo", project_dir=str(proj_b))

        assert store_a._file_path != store_b._file_path
        assert store_a._file_path == _xdg.project_memory_path(str(proj_a))


class TestExperienceStatsOldest:
    def test_oldest_non_empty_and_le_newest(self, tmp_path: Path) -> None:
        from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore

        store = ExperienceMemoryStore()
        store.load(scope="global")
        # One entry with a blank first_seen (as if migrated from an old
        # record) alongside a normal one — min() over strings must not let
        # "" win just because it sorts first lexicographically.
        store.entries = [
            ExperienceEntry(id="1", type="impact_high", first_seen="", last_seen="2020-01-01T00:00:00"),
            ExperienceEntry(id="2", type="impact_high", first_seen="2021-06-01T00:00:00",
                             last_seen="2022-01-01T00:00:00"),
        ]

        stats = store.stats()
        assert stats["oldest"] == "2021-06-01T00:00:00"
        assert stats["oldest"] <= stats["newest"]

    def test_empty_store_reports_none_not_empty_string(self) -> None:
        from vise.engines.experience_memory import ExperienceMemoryStore

        store = ExperienceMemoryStore()
        store.load(scope="global")
        store.entries = []

        stats = store.stats()
        assert stats["oldest"] is None
        assert stats["oldest"] != ""


class TestDeployAgentsInlineRemoved:
    def test_deploy_agents_inline_no_longer_exists(self) -> None:
        from vise.tools import goal

        assert not hasattr(goal, "_deploy_agents_inline")

    def test_goal_bootstrap_never_claims_agents_deployed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """goal_bootstrap's result must not claim agents were deployed —
        the only code path that ever populated agents_deployed was dead
        (ImportError on a module that doesn't exist)."""
        from vise.tools import goal as goal_tools

        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text("{}")

        class _FakeMCP:
            def __init__(self) -> None:
                self.registered: dict = {}

            def tool(self, *a, **kw):
                def deco(fn):
                    self.registered[fn.__name__] = fn
                    return fn
                return deco

        mcp = _FakeMCP()
        goal_tools.register_goal(mcp)
        tool_fn = mcp.registered.get("goal_bootstrap")
        assert tool_fn is not None, "goal_bootstrap tool not registered"

        result = tool_fn(
            goal="test goal",
            project_dir=str(tmp_path),
            synthesize_workflow=False,
        )

        assert result["agents_deployed"] == []
        assert "next" in result
        assert "deployed agents" not in result["next"]
