"""Files two processes share, written by one of them without care.

Tier T3. Every file here has a documented second reader in another process —
the MCP server reads what the CLI wrote, a hook reads what a tool wrote — and
`Path.write_text` opens mode 'w', which truncates before a single byte of the
new content lands. A reader arriving in that window sees an empty or partial
file.

What that costs differs per file, and the worst case is not data loss. It is
`graph_state.json`: the PreToolUse enforcer is registered on matcher `*`, so it
reads that file on every tool call in every session and every `claude -p`
worker. A torn read there sends it down its fail-open path, and a tool the
active node blocks is approved. The gate does not report that it could not
evaluate; it reports approval.

The correct pattern is already in this repo — `experience_memory.save` uses
`mkstemp` in the destination directory, `fsync`, then `replace`. These tests
say the rest of it has to use the same one.
"""
from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from vise.engines import goal_state, graph_state
from vise.engines.graph_engine import GraphState
from vise.runtime.contracts import RunBudget, RunSpec
from vise.runtime.state import RunState

# A payload big enough that a truncating write leaves a real window open.
_BULK = {f"node-{i}": f"value-{i}" * 40 for i in range(400)}


def _hammer_reads(path: Path, stop_after: float, results):
    """Read and parse as fast as possible; record anything unparseable."""
    bad = 0
    reads = 0
    while time.monotonic() < stop_after:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text:
            bad += 1
            continue
        reads += 1
        try:
            json.loads(text)
        except json.JSONDecodeError:
            bad += 1
    results["reads"] = reads
    results["bad"] = bad


def _concurrent_torn_reads(path: Path, write) -> tuple[int, int]:
    """Run `write` in a loop while another process parses the file."""
    manager = multiprocessing.Manager()
    results = manager.dict()
    deadline = time.monotonic() + 2.0
    reader = multiprocessing.Process(
        target=_hammer_reads, args=(path, deadline, results)
    )
    reader.start()
    while time.monotonic() < deadline:
        write()
    reader.join(timeout=10)
    return int(results.get("reads", 0)), int(results.get("bad", 0))


# --- graph state: the file the enforcer reads on every single tool call ----


def test_graph_state_is_never_readable_in_a_torn_state(tmp_path):
    state = GraphState()
    state.active_graph = "feature-dev"
    state.current_nodes = ["implement"]
    state.node_outputs = dict(_BULK)
    graph_state.save_graph_state(str(tmp_path), state)
    path = graph_state.get_graph_state_file(str(tmp_path))

    def write():
        graph_state.save_graph_state(str(tmp_path), state)

    reads, bad = _concurrent_torn_reads(path, write)

    assert reads > 0, "the reader never got to read anything"
    assert bad == 0, (
        f"{bad} of {reads + bad} reads saw a truncated or empty file — the "
        f"PreToolUse enforcer reads this on every tool call and approves on a "
        f"parse failure"
    )


def test_save_graph_state_leaves_no_fixed_name_temp_behind(tmp_path):
    state = GraphState()
    state.active_graph = "feature-dev"
    graph_state.save_graph_state(str(tmp_path), state)

    leftovers = list(graph_state.get_graph_state_file(str(tmp_path)).parent.glob("*.tmp"))
    assert not leftovers, f"temp files survived the write: {leftovers}"


# --- run state: the CLI writes it, the MCP server reads it ----------------


def _run_state(tmp_path) -> RunState:
    spec = RunSpec(run_id="r1", goal="g", project_dir=str(tmp_path),
                   budget=RunBudget())
    state = RunState(spec=spec)
    for i in range(300):
        state.record(f"task-{i}")
    return state


def test_run_state_is_never_readable_in_a_torn_state(tmp_path):
    state = _run_state(tmp_path)
    path = state.save(tmp_path)

    reads, bad = _concurrent_torn_reads(path, lambda: state.save(tmp_path))

    assert reads > 0
    assert bad == 0, f"{bad} of {reads + bad} reads saw a partial state file"


def test_loading_a_truncated_run_state_reports_rather_than_raises(tmp_path):
    """`run_status` and `run_budget` serve this from the server process.

    An unparseable file has to come back as "no such run", which every caller
    already handles, rather than as a traceback out of an MCP tool.
    """
    path = tmp_path / "runs" / "r1" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"spec": {"run_id"', encoding="utf-8")

    assert RunState.load(tmp_path, "r1") is None


def test_loading_a_run_state_that_is_not_an_object_reports_rather_than_raises(tmp_path):
    path = tmp_path / "runs" / "r1" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")

    assert RunState.load(tmp_path, "r1") is None


def test_a_readable_run_state_still_loads(tmp_path):
    """The guard: degrading on garbage must not degrade on a real file."""
    state = _run_state(tmp_path)
    state.save(tmp_path)

    loaded = RunState.load(tmp_path, "r1")

    assert loaded is not None and loaded.spec.run_id == "r1"
    assert len(loaded.tasks) == 300


# --- goal state: two writers, one fixed temp filename ---------------------


def test_goal_state_is_never_readable_in_a_torn_state(tmp_path):
    goal_state.set_goal(str(tmp_path), "a goal long enough to matter " * 60)
    path = goal_state._path_for(str(tmp_path))

    def write():
        goal_state.update_goal(str(tmp_path), notes="x" * 2000)

    reads, bad = _concurrent_torn_reads(path, write)

    assert reads > 0
    assert bad == 0, f"{bad} of {reads + bad} reads saw a partial goal file"


def _hammer_profile_reads(project_dir: str, stop_after: float, results):
    """Resolve a check as fast as possible; record every unusable profile."""
    from vise.engines import quality_profile

    bad = 0
    reads = 0
    while time.monotonic() < stop_after:
        resolved = quality_profile.resolve_check(project_dir, "unit")
        if isinstance(resolved, quality_profile.UnboundCheck):
            bad += 1
            continue
        reads += 1
    results["reads"] = reads
    results["bad"] = bad


def test_the_quality_profile_is_never_readable_as_no_profile_mid_write(tmp_path):
    """A torn `.vise/quality.yaml` is a silent NO_PROFILE: every check skip-passes.

    The second reader is the node gate, which resolves this file on every
    traverse. `write_text` truncates first, and a reader in that window gets
    unparseable YAML — which `quality_profile` correctly degrades to
    NO_PROFILE rather than raising, and NO_PROFILE means every check reports
    `asserted`/`unverified` and reads as green. Losing the profile for one
    traverse costs the whole gate, quietly, which is why the write bootstrap
    makes has to be the atomic one.
    """
    from vise.cli.bootstrap_cmd import detect, render
    from vise.core.atomic import write_atomic

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    profile = tmp_path / ".vise" / "quality.yaml"
    profile.parent.mkdir(parents=True)
    # Bulk comments, so a truncating write would leave a real window open.
    body = render(detect(tmp_path)) + "\n".join(f"# pad {i}" for i in range(4000)) + "\n"
    write_atomic(profile, body)

    manager = multiprocessing.Manager()
    results = manager.dict()
    deadline = time.monotonic() + 2.0
    reader = multiprocessing.Process(
        target=_hammer_profile_reads, args=(str(tmp_path), deadline, results)
    )
    reader.start()
    while time.monotonic() < deadline:
        write_atomic(profile, body)
    reader.join(timeout=10)

    reads, bad = int(results.get("reads", 0)), int(results.get("bad", 0))
    assert reads > 0, "the reader never got to read anything"
    assert bad == 0, (
        f"{bad} of {reads + bad} resolutions saw an unusable profile — every "
        f"quality check skip-passes on one of those, and says nothing"
    )


@pytest.mark.parametrize("module,fn", [
    ("vise.engines.goal_state", "_write"),
    ("vise.tools.goal", "_write_settings_atomic"),
    ("vise.cli.bootstrap_cmd", "_cmd_bootstrap"),
])
def test_atomic_writers_do_not_use_a_predictable_temp_name(module, fn):
    """A fixed `.json.tmp` is one file two processes both write.

    The rename is atomic; the write into a shared temp is not, so one process
    publishes the other's half-written bytes. `mkstemp` in the destination
    directory is what makes the pattern actually hold — and this repo already
    uses it in `experience_memory.save`.
    """
    import importlib
    import inspect

    src = inspect.getsource(getattr(importlib.import_module(module), fn))
    assert 'with_suffix(".json.tmp")' not in src, (
        f"{module}.{fn} builds its temp path by name, so two processes share it"
    )
    assert "write_atomic" in src, (
        f"{module}.{fn} does not go through the one atomic writer"
    )
