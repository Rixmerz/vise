"""Tests for experience_recorder.py junk-path/junk-pattern filtering.

Coverage:
  - _is_junk_file: generated dirs, lockfiles, minified bundles, hashed basenames
  - _is_junk_pattern: bare-root globs excluded, category-tail root patterns kept
  - main(): a commit touching only junk files records nothing; a commit mixing
    junk + real files records only the real one
"""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import vise.hooks.experience_recorder as rec


# ---------------------------------------------------------------------------
# _is_junk_file
# ---------------------------------------------------------------------------

class TestIsJunkFile:
    def test_rejects_fresh_build_output(self):
        assert rec._is_junk_file("_fresh/client/assets/xGhrzv.js") is True

    def test_rejects_node_modules(self):
        assert rec._is_junk_file("node_modules/foo/index.js") is True

    def test_rejects_dist(self):
        assert rec._is_junk_file("dist/bundle.js") is True

    def test_rejects_vite_cache(self):
        assert rec._is_junk_file("_fresh/client/.vite/manifest.json") is True

    def test_rejects_pycache(self):
        assert rec._is_junk_file("src/__pycache__/mod.pyc") is True

    def test_rejects_lockfile(self):
        assert rec._is_junk_file("deno.lock") is True

    def test_rejects_minified_js(self):
        assert rec._is_junk_file("assets/vendor.min.js") is True

    def test_rejects_target_debug(self):
        assert rec._is_junk_file("target/debug/build/foo.rs") is True

    def test_rejects_hashed_basename(self):
        assert rec._is_junk_file("src/chunk-4f2a9c1e.js") is True

    def test_accepts_real_source_file(self):
        assert rec._is_junk_file("src/services/authService.ts") is False

    def test_accepts_readme(self):
        assert rec._is_junk_file("README.md") is False


# ---------------------------------------------------------------------------
# _is_junk_pattern
# ---------------------------------------------------------------------------

class TestIsJunkPattern:
    def test_rejects_bare_root_json(self):
        assert rec._is_junk_pattern("./*.json") is True

    def test_rejects_bare_root_md(self):
        assert rec._is_junk_pattern("./*.md") is True

    def test_rejects_bare_root_toml(self):
        assert rec._is_junk_pattern("./*.toml") is True

    def test_accepts_category_tail_pattern(self):
        # "./*Service.ts" has a real category tail — still useful
        assert rec._is_junk_pattern("./*Service.ts") is False

    def test_accepts_non_root_pattern(self):
        assert rec._is_junk_pattern("src/services/*.ts") is False


# ---------------------------------------------------------------------------
# main() integration — junk filtering end to end
# ---------------------------------------------------------------------------

def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    # `git diff-tree HEAD` (no --root) yields nothing for a root commit —
    # seed a parent commit so the real test commit has a diffable parent.
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "chore: init"], cwd=repo, check=True)
    return repo


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", rel], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _run_recorder(monkeypatch, tmp_path, repo: Path) -> str:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    stdin_data = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m test"},
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_data))
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_stdout)
    monkeypatch.setattr("sys.stderr", captured_stderr)
    rec.main()
    return captured_stderr.getvalue()


class TestMainWritesBothStores:
    """The write loop itself. Everything else here tests the filters it feeds."""

    def _stores(self, tmp_path: Path) -> tuple[Path, Path]:
        root = tmp_path / "xdg" / "vise"
        [project] = (root / "project_memories").glob("*/experience_memory.json")
        return project, root / "experience_memory.json"

    def test_each_store_gets_its_own_entry_not_a_shared_dict(self, monkeypatch, tmp_path):
        """`upsert` fills in an id and a first_seen on the dict it is handed. One
        dict passed to both files carries the first store's id into the second and
        calls them the same entry — and whichever scope was set last wins."""
        repo = _init_repo(tmp_path)
        _commit(repo, {"src/services/authService.ts": "export const auth = 1;"},
                "fix: token refresh raced with logout")
        _run_recorder(monkeypatch, tmp_path, repo)

        project_path, global_path = self._stores(tmp_path)
        [project] = json.loads(project_path.read_text())["entries"]
        [shared] = json.loads(global_path.read_text())["entries"]

        assert project["file_pattern"] == shared["file_pattern"]
        assert project["id"] != shared["id"], "one dict was written to both files"
        assert project["scope"] == "project"
        assert shared["scope"] == "global"

    def test_two_commits_on_one_glob_become_one_counted_entry(self, monkeypatch, tmp_path):
        repo = _init_repo(tmp_path)
        _commit(repo, {"src/services/authService.ts": "const a = 1;"},
                "fix: token refresh raced with logout")
        _run_recorder(monkeypatch, tmp_path, repo)
        _commit(repo, {"src/services/authService.ts": "const a = 2;"},
                "fix: rate limiter counted retries twice")
        _run_recorder(monkeypatch, tmp_path, repo)

        _, global_path = self._stores(tmp_path)
        [entry] = json.loads(global_path.read_text())["entries"]
        assert entry["occurrences"] == 2, (
            "the second commit's lesson reached the entry; under the hook's own "
            "key it was a second entry the store's next merge deleted"
        )

    def test_the_hook_applies_the_cap_rather_than_leaving_it_to_the_store(
        self, monkeypatch, tmp_path,
    ):
        """Skipping it did not add headroom — it left the trim to whoever saved
        next, on a file this hook had already grown past the cap."""
        from vise.core.experience_rules import MAX_ENTRIES

        repo = _init_repo(tmp_path)
        store_path = tmp_path / "xdg" / "vise" / "experience_memory.json"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps({"entries": [
            {"type": "fix", "file_pattern": f"src/a{i}/*.py", "domain": "api",
             "description": f"e{i}", "confidence": 0.9, "occurrences": 1,
             "last_seen": "2026-09-01T00:00:00"}
            for i in range(MAX_ENTRIES + 50)
        ]}))

        _commit(repo, {"src/services/authService.ts": "const a = 1;"}, "fix: something")
        _run_recorder(monkeypatch, tmp_path, repo)

        assert len(json.loads(store_path.read_text())["entries"]) == MAX_ENTRIES


class TestMainJunkFiltering:
    def test_commit_touching_only_readme_at_root_records_nothing(self, monkeypatch, tmp_path):
        repo = _init_repo(tmp_path)
        _commit(repo, {"README.md": "hello"}, "docs: update readme")
        stderr = self._run_and_check_store(monkeypatch, tmp_path, repo)
        assert stderr == "" or "Experience recorded" not in stderr

    def test_commit_touching_only_junk_records_nothing(self, monkeypatch, tmp_path):
        repo = _init_repo(tmp_path)
        _commit(repo, {"_fresh/client/assets/xGhrzv.js": "junk"}, "feat: build output")
        self._run_and_check_store(monkeypatch, tmp_path, repo, expect_entries=0)

    def test_commit_with_junk_and_real_file_records_only_real(self, monkeypatch, tmp_path):
        repo = _init_repo(tmp_path)
        _commit(
            repo,
            {
                "_fresh/client/assets/xGhrzv.js": "junk",
                "src/services/authService.ts": "export const auth = 1;",
            },
            "feat: add auth service",
        )
        self._run_and_check_store(monkeypatch, tmp_path, repo, expect_entries=1)

    def _run_and_check_store(self, monkeypatch, tmp_path, repo, expect_entries=None):
        stderr = _run_recorder(monkeypatch, tmp_path, repo)
        store_path = tmp_path / "xdg" / "vise" / "experience_memory.json"
        if expect_entries is None:
            return stderr
        if expect_entries == 0:
            assert not store_path.exists() or json.loads(store_path.read_text()).get("entries") == []
        else:
            entries = json.loads(store_path.read_text())["entries"]
            assert len(entries) == expect_entries
            for e in entries:
                assert "_fresh" not in e["file_pattern"]
        return stderr
