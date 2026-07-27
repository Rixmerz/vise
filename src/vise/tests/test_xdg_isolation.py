"""Regression coverage for the hooks/tools XDG split-brain bug.

Prior to the fix, hard-blocking hooks (graph_enforcer.py et al.) hardcoded
``Path.home() / ".local" / "share" / "vise"`` while the MCP tools honored
``$XDG_DATA_HOME`` via ``vise.core.paths.data_dir()``. With that env var
set, the two wrote to different trees and never saw each other:

- the phase-gate enforcer never blocked anything (it read a graph_state.json
  that was never written, because the tools wrote to the XDG path), and
- 60 recorded experiences were invisible to every query tool.

No test existed that ran a hook with $XDG_DATA_HOME pointed at a tmpdir and
asserted the block actually happened. This file is that test, plus the
structural guards that stop a 17th hardcoded site from reintroducing the
same bug.
"""
from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENFORCER_SCRIPT = REPO_ROOT / "src" / "vise" / "hooks" / "graph_enforcer.py"


def _write_blocking_state(project: Path, data_home: Path) -> None:
    """Write a graph.yaml + graph_state.json that blocks Write at the
    current node, laid out exactly as core.state_paths resolves it."""
    (project / ".claude" / "workflow").mkdir(parents=True, exist_ok=True)
    (project / ".claude" / "workflow" / "graph.yaml").write_text(
        "nodes:\n"
        "  - id: implement\n"
        "    tools_blocked:\n"
        "      - Write\n"
        "edges:\n"
    )
    state_dir = data_home / "vise" / "states" / project.name
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "graph_state.json").write_text(json.dumps({
        "active_graph": "test-graph",
        "current_nodes": ["implement"],
        "node_visits": {"implement": 1},
        "execution_path": [],
    }))


def _run_enforcer(interpreter_args: list[str], project: Path, data_home: Path,
                   home: Path) -> dict:
    env = {
        "HOME": str(home),
        "XDG_DATA_HOME": str(data_home),
        "CLAUDE_PROJECT_DIR": str(project),
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        [*interpreter_args, str(ENFORCER_SCRIPT)],
        input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x.txt"}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return json.loads(result.stdout)


class TestEnforcerBlocksViaXdgPath:
    """The crux repro: HOME and XDG_DATA_HOME point at DIFFERENT empty
    tmpdirs. If the enforcer silently fell back to ~/.local/share/vise
    (i.e. HOME), it would find no state there and approve everything —
    exactly the bug that shipped. Only reading via $XDG_DATA_HOME finds
    the state and blocks.

    Parametrized over two interpreters so BOTH of graph_enforcer.py's
    path-resolution branches are exercised, not just whichever one the
    ambient environment happens to pick:

    - venv python (``sys.executable``): ``vise`` is importable via the
      editable-install site-packages .pth, so ``from vise.hooks import
      _xdg`` succeeds — this is the "package installed" branch.
    - system python3 with ``-I`` (isolated mode: no user-site, no
      PYTHONPATH, no script-dir sys.path[0] injection*): ``import vise``
      is guaranteed to fail, forcing graph_enforcer.py into its inline
      stdlib-only fallback mirror of _xdg.data_dir(). That mirror is
      hand-duplicated logic and is the branch a bare-standalone hook
      deployment actually runs — it must independently prove the block,
      not just inherit a pass from the venv-python case.

    (* -I still keeps the script's own directory off sys.path for `python
    script.py`, which is what actually matters here — the script never
    does a relative import of its own package.)
    """

    @pytest.mark.parametrize(
        "interpreter_args,expect_real_xdg_import",
        [
            pytest.param([sys.executable], True, id="venv-python-real-xdg-import"),
            pytest.param(["/usr/bin/python3", "-I"], False, id="system-python-inline-mirror"),
        ],
    )
    def test_enforcer_blocks_via_xdg_path_subprocess(
        self, tmp_path: Path, interpreter_args: list[str], expect_real_xdg_import: bool
    ) -> None:
        home = tmp_path / "unused-home"
        home.mkdir()
        data_home = tmp_path / "data"
        project = tmp_path / "myproject"
        project.mkdir()
        _write_blocking_state(project, data_home)

        # Confirm which branch this interpreter actually takes, so a
        # future environment change (e.g. system python3 gaining a
        # user-site vise install) can't silently collapse both
        # parametrizations onto the same code path.
        import_check = subprocess.run(
            [*interpreter_args, "-c", "import vise"],
            capture_output=True,
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
            timeout=10,
        )
        actually_importable = import_check.returncode == 0
        assert actually_importable is expect_real_xdg_import, (
            f"expected `import vise` to {'succeed' if expect_real_xdg_import else 'fail'} "
            f"under {interpreter_args}, but it did not — this parametrization is no "
            f"longer exercising the branch its id claims"
        )

        decision = _run_enforcer(interpreter_args, project, data_home, home)
        assert decision["decision"] == "block", decision
        assert "implement" in decision["message"]

        # Sanity: nothing was written under the unused HOME. If the hook
        # had resolved the legacy ~/.local/share/vise path it would have
        # found no state there (silent no-op), not landed in HOME at all —
        # this asserts the negative directly: no vise dir under HOME.
        assert not (home / ".local" / "share" / "vise").exists()

    def test_enforcer_approves_when_state_only_under_home(self, tmp_path: Path) -> None:
        """Mirror check: if the state is written under the legacy HOME
        path instead of XDG_DATA_HOME, the enforcer must NOT find it and
        must approve — proving it does not silently fall back to HOME
        when XDG_DATA_HOME is set."""
        home = tmp_path / "home"
        legacy_data_home = home / ".local" / "share"
        data_home = tmp_path / "data"  # real XDG target, left empty
        project = tmp_path / "myproject"
        project.mkdir()
        _write_blocking_state(project, legacy_data_home)  # wrong location on purpose

        env = {
            "HOME": str(home),
            "XDG_DATA_HOME": str(data_home),
            "CLAUDE_PROJECT_DIR": str(project),
            "PATH": "/usr/bin:/bin",
        }
        result = subprocess.run(
            [sys.executable, str(ENFORCER_SCRIPT)],
            input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x.txt"}}),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        decision = json.loads(result.stdout)
        # No project-local fallback exists either, so this fails open.
        assert decision["decision"] == "approve", result.stdout


class TestHooksAndToolsAgreeOnPath:
    """Enumerate every helper in _xdg.py (rather than spot-checking two)
    so a newly added helper that forgets to honor $XDG_DATA_HOME fails
    this test immediately."""

    def test_xdg_data_dir_matches_core_paths_data_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from vise.core import paths as core_paths
        from vise.hooks import _xdg

        xdg_home = tmp_path / "data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))

        assert core_paths.data_dir() == _xdg.data_dir()
        assert core_paths.data_dir() == xdg_home / "vise"

    def test_every_xdg_helper_resolves_under_xdg_data_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Every public helper in _xdg.py must resolve to a path rooted
        at $XDG_DATA_HOME/vise. Iterates __all__ so a new helper that
        forgets to route through data_dir() is caught automatically."""
        from vise.hooks import _xdg

        xdg_home = tmp_path / "data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_home))
        # telemetry_path()/usage_dir() have their own override env vars
        # (VISE_TELEMETRY_DIR/VISE_USAGE_DIR) that take precedence over
        # data_dir() by design; clear them so this test asserts the
        # $XDG_DATA_HOME fallback behavior, not whatever the ambient dev
        # environment happens to have set.
        monkeypatch.delenv("VISE_TELEMETRY_DIR", raising=False)
        monkeypatch.delenv("VISE_USAGE_DIR", raising=False)
        expected_root = xdg_home / "vise"

        assert _xdg.__all__, "expected at least one public helper in _xdg.py"

        for name in _xdg.__all__:
            fn = getattr(_xdg, name)
            sig = inspect.signature(fn)
            # Supply a dummy positional arg for helpers that take one
            # (project_dir / project_name); all such params are strings.
            args = ["dummy"] * len(sig.parameters)
            result = fn(*args)
            assert isinstance(result, Path), f"{name} did not return a Path"
            assert result == expected_root or expected_root in result.parents, (
                f"_xdg.{name}() = {result!r} is not rooted at {expected_root!r} "
                f"— this helper does not honor $XDG_DATA_HOME"
            )


class TestNoHardcodedLegacyPath:
    """Guard against reintroducing a hardcoded ~/.local/share literal
    anywhere a hook or core/engine path resolver could pick it up. 16
    such sites were fixed in the audit this test locks in; this stops
    the 17th.

    Exceptions are files that legitimately need the literal legacy path:
    - _xdg.py: the single source of truth computes the DEFAULT fallback
      (`Path.home() / ".local" / "share"`) when $XDG_DATA_HOME is unset —
      that's the spec-correct default, not a bug.
    - graph_enforcer.py: carries an inline stdlib-only mirror of _xdg.py
      for when `vise.hooks._xdg` cannot be imported (hard-blocking hook,
      must never crash before printing a decision).
    - xdg_migrate.py: reads the LEGACY directory ON PURPOSE to migrate it
      into the XDG-resolved one.
    """

    ALLOWED_FILES = {
        "src/vise/hooks/_xdg.py",
        "src/vise/hooks/graph_enforcer.py",
        "src/vise/core/xdg_migrate.py",
    }

    SCAN_DIRS = ("src/vise/hooks", "src/vise/core", "src/vise/engines")

    # Matches ".local" as a substring of ANY string literal (not just a
    # bare '.local' token) so disguised forms like
    # Path(os.path.expanduser("~/.local/share/vise")) are still caught.
    LEGACY_LITERAL_RE = re.compile(
        r"""(["'])((?:(?!\1).)*\.local(?:(?!\1).)*)\1"""
    )

    # A line that merely MENTIONS the legacy path in prose (module/function
    # docstrings explaining the layout, e.g. "~/.local/share/vise/ by
    # default") is not a hardcode — only lines that actually CONSTRUCT a
    # path are. Restrict to lines that pair the literal with a path-
    # building call so docstrings don't drown out real offenders.
    PATH_CONSTRUCTION_MARKER_RE = re.compile(
        r"""Path\(|\.home\(\)|expanduser\(|os\.path\."""
    )

    def test_no_new_hardcoded_legacy_path_literals(self) -> None:
        offenders: list[str] = []
        for scan_dir in self.SCAN_DIRS:
            base = REPO_ROOT / scan_dir
            if not base.exists():
                continue
            for py_file in base.rglob("*.py"):
                rel = py_file.relative_to(REPO_ROOT).as_posix()
                if rel in self.ALLOWED_FILES:
                    continue
                for lineno, line in enumerate(
                    py_file.read_text(encoding="utf-8").splitlines(), start=1
                ):
                    if self.LEGACY_LITERAL_RE.search(line) and \
                            self.PATH_CONSTRUCTION_MARKER_RE.search(line):
                        offenders.append(f"{rel}:{lineno}: {line.strip()}")

        assert offenders == [], (
            f"Found hardcoded '.local'/'share' path literals outside the "
            f"documented exception list: {offenders}. Route through "
            f"vise.hooks._xdg / vise.core.paths.data_dir() instead."
        )


class TestXdgDataHomeFallback:
    """Per the XDG Base Directory spec, a relative $XDG_DATA_HOME is
    invalid and must be ignored — fall back to the default. _xdg.py
    implements this; lock it in."""

    def test_unset_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vise.hooks import _xdg

        monkeypatch.delenv("XDG_DATA_HOME", raising=False)

        assert _xdg.data_dir() == Path.home() / ".local" / "share" / "vise"

    def test_empty_string_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vise.hooks import _xdg

        monkeypatch.setenv("XDG_DATA_HOME", "")

        assert _xdg.data_dir() == Path.home() / ".local" / "share" / "vise"

    def test_relative_value_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vise.hooks import _xdg

        monkeypatch.setenv("XDG_DATA_HOME", "relative/path")

        assert _xdg.data_dir() == Path.home() / ".local" / "share" / "vise"

    def test_absolute_value_is_honored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from vise.hooks import _xdg

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        assert _xdg.data_dir() == tmp_path / "vise"
