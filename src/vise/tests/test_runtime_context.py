"""The context resolver's job is what it leaves out.

Every cap here is asserted, and so is the fact that a truncation says how much
it dropped: a silently truncated context is one the worker reasons from as
though it were complete.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from vise.engines.graph_engine import Task
from vise.runtime.context import ContextResolver


def _tree(tmp_path: Path, *rel: str) -> Path:
    for r in rel:
        p = tmp_path / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    return tmp_path


def _resolver(tmp_path: Path, **kw) -> ContextResolver:
    kw.setdefault("include_experience", False)
    kw.setdefault("include_diff", False)
    return ContextResolver(project_dir=tmp_path, **kw)


def test_owned_files_are_listed_and_unowned_ones_are_not(tmp_path):
    _tree(tmp_path, "src/auth/token.py", "src/db/schema.py", "web/app.js")
    task = Task(id="t", name="t", ownership=["src/auth/**"])
    lines = _resolver(tmp_path).resolve(task)
    text = "\n".join(lines)
    assert "src/auth/token.py" in text
    assert "src/db/schema.py" not in text


def test_a_task_owning_everything_gets_no_file_list(tmp_path):
    """Walking a repo to hand a worker 40 arbitrary paths is worse than none —
    they look like a selection and they are not."""
    _tree(tmp_path, "a.py", "b.py")
    assert _resolver(tmp_path).owned_files(Task(id="t", name="t")) == []


def test_new_ground_is_said_rather_than_left_blank(tmp_path):
    _tree(tmp_path, "src/other.py")
    lines = _resolver(tmp_path).resolve(Task(id="t", name="t", ownership=["src/new/**"]))
    assert any("new ground" in line for line in lines)


def test_the_file_list_is_capped_and_says_how_much_it_dropped(tmp_path):
    _tree(tmp_path, *[f"src/f{i}.py" for i in range(30)])
    lines = _resolver(tmp_path, max_files=5).resolve(
        Task(id="t", name="t", ownership=["src/**"])
    )
    listed = [line for line in lines if line.startswith("  src/")]
    assert len(listed) == 5
    assert any("more" in line and "narrow the ownership" in line for line in lines)


def test_noise_directories_are_skipped(tmp_path):
    _tree(tmp_path, "src/real.py", "src/node_modules/dep/index.js", "src/__pycache__/x.pyc")
    owned = _resolver(tmp_path).owned_files(Task(id="t", name="t", ownership=["src/**"]))
    assert owned == ["src/real.py"]


def test_a_missing_project_directory_resolves_to_nothing(tmp_path):
    resolver = _resolver(tmp_path / "gone")
    assert resolver.resolve(Task(id="t", name="t", ownership=["src/**"])) != ()
    assert resolver.owned_files(Task(id="t", name="t", ownership=["src/**"])) == []


def test_the_diff_lists_names_and_counts_not_content(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True,
                   capture_output=True)
    (tmp_path / "changed.py").write_text("secret = 'do not leak'\n", encoding="utf-8")
    lines = ContextResolver(project_dir=tmp_path, include_experience=False).diff_lines()
    text = "\n".join(lines)
    assert "changed.py" in text
    assert "do not leak" not in text


def test_the_diff_is_capped_and_says_how_much_it_dropped(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("x\n", encoding="utf-8")
    lines = ContextResolver(
        project_dir=tmp_path, include_experience=False, max_diff_paths=3
    ).diff_lines()
    assert any("+7 more not listed" in line for line in lines)


def test_the_diff_degrades_to_nothing_outside_a_repository(tmp_path):
    assert ContextResolver(project_dir=tmp_path).diff_lines() == []


def test_the_resolver_never_raises_when_git_is_missing(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("git missing")
    monkeypatch.setattr(subprocess, "run", boom)
    assert ContextResolver(project_dir=tmp_path).diff_lines() == []


def test_experience_lines_degrade_to_nothing_when_the_store_fails(tmp_path, monkeypatch):
    import vise.engines.experience_memory as mem

    def boom(*_a, **_k):
        raise RuntimeError("no store")
    monkeypatch.setattr(mem, "get_project_experience_store", boom)
    resolver = ContextResolver(project_dir=tmp_path)
    assert resolver.experience_lines(["src/a.py"]) == []


def test_experience_lines_surface_a_recorded_learning(tmp_path):
    from vise.engines.experience_memory import (
        ExperienceEntry,
        get_project_experience_store,
    )

    store = get_project_experience_store(str(tmp_path))
    store.record(ExperienceEntry(
        type="tension_caused",
        file_pattern="src/auth/*.py",
        description="this module had timezone-offset problems",
        keywords=["auth"],
        domain="auth",
    ))
    store.save()
    lines = ContextResolver(project_dir=tmp_path).experience_lines(["src/auth/token.py"])
    assert any("timezone-offset" in line for line in lines)
