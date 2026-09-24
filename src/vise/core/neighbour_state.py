"""What the neighbours left on disk, read without calling them.

vise cannot call livespec, layout-inspector or flowtrace — MCP has no
server-to-server channel — and every asset that names their tools is therefore
advice to an agent, not something vise can check. That was taken to mean vise
could know nothing about them, which is false: two of the three leave durable
artifacts in the repository, and a file is not a tool call.

    livespec   `.mcp-docs/docs.db`      — SQLite. Was this repo ever indexed?
    flowtrace  `.flowtrace/*.jsonl`     — one JSON object per line. What ran?
    Graphify   `graphify-out/graph.json`— NetworkX node-link. Read by livespec.
    delta-cube `$DCC_DATA_DIR/dcc.db`   — SQLite, one per machine. Which files
                                          of this repo are points in it, and
                                          how many tensions are still open.
    tasky      `~/.local/share/tasky/`  — SQLite, one ledger per machine.
               `tasky.db`                 How many sessions of this repo it
                                          holds, and how many problems are open.

That is the difference between a phase that *asks an agent* whether an index
exists and a phase that *knows*. The decouple workflow's first step is
"`compute_index_status()`, and STOP if there is no index" — a refusal the agent
could re-weigh, written against a tool that does not exist. Here it is a
function.

Rules this module holds itself to, because it reads someone else's files:

**Never raise.** A caller is a PreToolUse hook or a validator. Both have a
contract about not taking the session down, and neither can afford a
`sqlite3.DatabaseError` from a database another process is mid-write on. Every
public function returns a state object; the failure case is a state, not an
exception.

**Unknown is not absent.** A database that will not open is not an unindexed
repo, and the two must not collapse: "no index" makes a gate refuse, "cannot
tell" must make it stand down. Every state carries `known`.

**Touch as little schema as possible.** Only `index_run` (did an index finish)
and `external_ingest` (which graph was ingested, migration 23). Both are read
defensively: a missing table is an older livespec, not an error. Counting
symbols or reading `symbol_edge` would couple vise to a schema it does not own
and gains nothing a gate needs.

delta-cube gets the same treatment, one layer wider, because its database is
not per repo: `code_points.file_path` is the only thing that says which
project a row belongs to, so every query here is scoped by that prefix. What
is read is what is durable — points, contracts, tensions. Smells, debt and
centrality are computed per call and never written, so no file can answer
"how many critical smells" and this module does not pretend to.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vise.core.neighbours import (
    DELTA_CUBE_DATA_DIR_ENV,
    DELTA_CUBE_DB_NAME,
    DELTA_CUBE_DEFAULT_DATA_DIR,
    TASKY_DB_NAME,
    TASKY_DEFAULT_DATA_HOME,
    TASKY_HOME_ENV,
    TASKY_XDG_SUBDIR,
)

#: Where livespec keeps its index, relative to the repo root.
LIVESPEC_DB = ".mcp-docs/docs.db"

#: Where `flowtrace run` writes, and the legacy name a hand-wired capture layer
#: uses. Newest match wins; both are checked because the CLI's own docs say the
#: bare name is only the default when someone wired a layer by hand.
FLOWTRACE_DIR = ".flowtrace"
FLOWTRACE_LEGACY = "flowtrace.jsonl"

#: Graphify's output. livespec's `[graph] external` default points here.
#:
#: Safe to read while Graphify may be rebuilding: it writes through a temp file
#: and `os.replace`, so a reader sees the old graph or the new one and never a
#: half-written file. Worth knowing because Graphify's git post-commit hook
#: rebuilds in the background — meaning a rebuild really can be in flight while
#: a vise gate or snapshot is reading. Checked against `graphify/paths.py`
#: (`write_json_atomic`) at 0.9.55, not assumed.
GRAPHIFY_GRAPH = "graphify-out/graph.json"

_SQLITE_TIMEOUT_S = 0.5


@dataclass(frozen=True)
class IndexState:
    """Whether livespec has an index for this repo."""

    #: True when the question was answered either way. False means the
    #: database exists but could not be read — a state a gate must not treat
    #: as "no index".
    known: bool = False
    #: True only when a finished index run is recorded.
    indexed: bool = False
    #: ISO timestamp of the last finished run, "" when unknown.
    last_run_at: str = ""
    #: One line naming what was found, for a message a person reads.
    detail: str = "not checked"

    @property
    def refuses(self) -> bool:
        """Should a phase that needs the symbol layer stand down?

        True only for a *known* absence. An unreadable database is not a
        refusal — it is a reason to say so and carry on, because refusing on
        "cannot tell" is how a gate teaches people to disable it.
        """
        return self.known and not self.indexed


@dataclass(frozen=True)
class TraceState:
    """The newest flowtrace log in this repo, if any."""

    path: Path | None = None
    events: int = 0
    #: Distinct trace ids seen. More than one means the file holds several
    #: interleaved executions and a conclusion drawn across them is worthless.
    traces: int = 0
    #: `enter`/`exit` events carrying an `error` object.
    errors: int = 0
    #: Lines that were not JSON. A partially written file is normal — the
    #: capture layer appends — so this is reported, never fatal.
    malformed: int = 0
    known: bool = False
    detail: str = "not checked"

    @property
    def empty(self) -> bool:
        """A trace file with no events. Almost always the package prefix.

        flowtrace's own command says so: an empty trace "looks like a bug in
        the code" and is not one. Saying which of the two it is here is the
        whole value of reading the file instead of asking.
        """
        return self.path is not None and self.events == 0


@dataclass(frozen=True)
class GraphState:
    """Graphify's graph, and whether livespec's index has ingested *it*."""

    path: Path | None = None
    #: livespec recorded an ingest from a graph at this path.
    ingested: bool = False
    #: "same" — mtime and size both match what was ingested.
    #: "differs" — the size changed, so it is definitely another graph.
    #: "unknown" — only the mtime moved. Graphify rewrites `graph.json` on
    #: every run, so that alone proves nothing; livespec hashes the content
    #: and can answer. vise does not reproduce its hash to guess.
    freshness: str = "unknown"
    detail: str = "not checked"

    @property
    def present(self) -> bool:
        return self.path is not None


def _connect(db: Path) -> sqlite3.Connection:
    """Read-only connection. The caller owns catching what this raises."""
    uri = f"file:{db.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=_SQLITE_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def index_state(project: Path | str) -> IndexState:
    """Has livespec indexed this repo? Answers without calling livespec."""
    try:
        db = Path(project) / LIVESPEC_DB
        if not db.is_file():
            return IndexState(
                known=True, indexed=False,
                detail=f"no {LIVESPEC_DB} — livespec has never indexed this repo",
            )
        conn = _connect(db)
        try:
            if not _has_table(conn, "index_run"):
                return IndexState(
                    known=True, indexed=False,
                    detail=f"{LIVESPEC_DB} exists but records no index run",
                )
            row = conn.execute(
                "SELECT finished_at FROM index_run "
                "WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return IndexState(
                known=True, indexed=False,
                detail=f"{LIVESPEC_DB} exists but no index run has finished",
            )
        when = str(row["finished_at"] or "")
        return IndexState(
            known=True, indexed=True, last_run_at=when,
            detail=f"livespec indexed this repo (last run {when or 'unknown'})",
        )
    except Exception as exc:  # noqa: BLE001 - a state, never an exception
        return IndexState(
            known=False,
            detail=f"could not read {LIVESPEC_DB}: {type(exc).__name__}: {exc}",
        )


def _trace_files(project: Path) -> list[Path]:
    found: list[Path] = []
    directory = project / FLOWTRACE_DIR
    if directory.is_dir():
        found.extend(p for p in directory.glob("*.jsonl") if p.is_file())
    legacy = project / FLOWTRACE_LEGACY
    if legacy.is_file():
        found.append(legacy)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found


def trace_state(project: Path | str, *, newer_than: float | None = None) -> TraceState:
    """Read the newest flowtrace log. ``newer_than`` is a POSIX timestamp.

    A phase that gates on "this run produced a trace" must not be satisfied by
    a trace from last week, so the caller passes the moment the phase started
    and gets `known=True, path=None` when nothing newer exists.
    """
    try:
        project = Path(project)
        files = _trace_files(project)
        if newer_than is not None:
            files = [p for p in files if p.stat().st_mtime >= newer_than]
        if not files:
            where = f"{FLOWTRACE_DIR}/*.jsonl"
            when = " newer than this phase" if newer_than is not None else ""
            return TraceState(
                known=True, detail=f"no {where}{when} — nothing was traced",
            )
        newest = files[0]
        events = traces = errors = malformed = 0
        seen: set[str] = set()
        with newest.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if not isinstance(row, dict):
                    malformed += 1
                    continue
                events += 1
                tid = row.get("trace_id")
                if isinstance(tid, str) and tid:
                    seen.add(tid)
                if row.get("error"):
                    errors += 1
        traces = len(seen)
        return TraceState(
            path=newest, events=events, traces=traces, errors=errors,
            malformed=malformed, known=True,
            detail=(
                f"{newest.name}: {events} events, {traces} trace id(s), "
                f"{errors} error event(s)"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a state, never an exception
        return TraceState(
            known=False,
            detail=f"could not read the trace: {type(exc).__name__}: {exc}",
        )


def error_signature(project: Path | str) -> tuple[str, ...]:
    """The failing call paths in the newest trace, as `module.class.method`.

    Used to compare a reproduction against a verification: the same signature
    still present after a fix means the fix did not reach the failure. Deriving
    this from the file rather than from `trace_find_error` is deliberate — a
    validator cannot make a tool call, and the events carry everything needed.
    """
    try:
        state = trace_state(project)
        if state.path is None:
            return ()
        paths: set[str] = set()
        with state.path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not row.get("error"):
                    continue
                parts = [
                    str(row.get(key) or "")
                    for key in ("module", "class", "method")
                ]
                name = ".".join(p for p in parts if p)
                if name:
                    paths.add(name)
        return tuple(sorted(paths))
    except Exception:  # noqa: BLE001 - a signature, never an exception
        return ()


def graph_state(project: Path | str) -> GraphState:
    """Is a Graphify graph present, and is livespec's ingest of it current?"""
    try:
        project = Path(project)
        graph = project / GRAPHIFY_GRAPH
        if not graph.is_file():
            return GraphState(detail=f"no {GRAPHIFY_GRAPH}")
        stat = graph.stat()
        db = project / LIVESPEC_DB
        if not db.is_file():
            return GraphState(
                path=graph, ingested=False, freshness="unknown",
                detail=f"{GRAPHIFY_GRAPH} present; no livespec index to ingest it",
            )
        conn = _connect(db)
        try:
            if not _has_table(conn, "external_ingest"):
                return GraphState(
                    path=graph, ingested=False, freshness="unknown",
                    detail=(
                        f"{GRAPHIFY_GRAPH} present; this index records no ingest "
                        "(livespec below v0.33, or nobody ran ingest_external_graph)"
                    ),
                )
            row = conn.execute(
                "SELECT graph_mtime, graph_size FROM external_ingest "
                "ORDER BY ingested_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return GraphState(
                path=graph, ingested=False, freshness="unknown",
                detail=f"{GRAPHIFY_GRAPH} present but never ingested",
            )
        size_matches = row["graph_size"] == stat.st_size
        mtime_matches = (
            row["graph_mtime"] is not None
            and abs(float(row["graph_mtime"]) - stat.st_mtime) < 1.0
        )
        if size_matches and mtime_matches:
            freshness, detail = "same", "the ingested graph is the one on disk"
        elif not size_matches:
            freshness, detail = (
                "differs",
                "the graph on disk is not the one that was ingested — re-run "
                "ingest_external_graph before trusting an edge count",
            )
        else:
            freshness, detail = (
                "unknown",
                "the graph was rewritten but is the same size; only livespec's "
                "content hash can say whether the ingest is current",
            )
        return GraphState(
            path=graph, ingested=True, freshness=freshness,
            detail=f"{GRAPHIFY_GRAPH}: {detail}",
        )
    except Exception as exc:  # noqa: BLE001 - a state, never an exception
        return GraphState(
            detail=f"could not read the graph state: {type(exc).__name__}: {exc}",
        )


@dataclass(frozen=True)
class CubeState:
    """Whether delta-cube holds this repo, and what it holds that is durable."""

    #: True when the question was answered either way. False means a database
    #: exists but could not be read — not "no index", and a gate must not
    #: refuse on it.
    known: bool = False
    #: True only when at least one file under this repo is a point.
    indexed: bool = False
    #: Points under this repo.
    files: int = 0
    #: ISO timestamp of the most recently updated point, "" when unknown.
    last_indexed_at: str = ""
    #: Tensions in `detected` status whose callee is under this repo. Zero is
    #: also what a repo nobody ran `cube_reindex` on reads — see `detail`.
    open_tensions: int = 0
    #: Whether any delta was ever recorded for a file here. Without one,
    #: `open_tensions == 0` means "never measured", not "healthy".
    reindexed: bool = False
    #: Which database was read, for a message a person acts on.
    db: str = ""
    detail: str = "not checked"

    @property
    def refuses(self) -> bool:
        """Should a phase that needs the cube stand down? Known absence only."""
        return self.known and not self.indexed


def delta_cube_db() -> Path:
    """The one database delta-cube writes on this machine."""
    base = os.environ.get(DELTA_CUBE_DATA_DIR_ENV) or DELTA_CUBE_DEFAULT_DATA_DIR
    return Path(base).expanduser() / DELTA_CUBE_DB_NAME


def cube_state(project: Path | str) -> CubeState:
    """Has delta-cube indexed this repo? Answers without calling delta-cube.

    Scoped by path prefix because the schema has no project column. A repo
    inside another indexed repo (a worktree, a vendored checkout) is counted
    under both, which is delta-cube's model and not something a reader can
    correct.
    """
    db = delta_cube_db()
    shown = db.as_posix().replace(str(Path.home()), "~", 1)
    try:
        root = Path(project).resolve().as_posix().rstrip("/") + "/"
        if not db.is_file():
            return CubeState(
                known=True, indexed=False, db=shown,
                detail=f"no {shown} — delta-cube has never indexed anything here",
            )
        conn = _connect(db)
        try:
            if not _has_table(conn, "code_points"):
                return CubeState(
                    known=True, indexed=False, db=shown,
                    detail=f"{shown} exists but has no code_points table",
                )
            like = (root.replace("%", "\\%").replace("_", "\\_") + "%",)
            row = conn.execute(
                "SELECT COUNT(*) AS n, MAX(updated_at) AS latest FROM code_points "
                "WHERE file_path LIKE ? ESCAPE '\\'",
                like,
            ).fetchone()
            files = int(row["n"] or 0)
            latest = str(row["latest"] or "")
            reindexed = False
            if files and _has_table(conn, "deltas"):
                hit = conn.execute(
                    "SELECT 1 FROM deltas d JOIN code_points cp ON d.code_point_id = cp.id "
                    "WHERE cp.file_path LIKE ? ESCAPE '\\' LIMIT 1",
                    like,
                ).fetchone()
                reindexed = hit is not None
            tensions = 0
            if files and _has_table(conn, "tensions") and _has_table(conn, "contracts"):
                hit = conn.execute(
                    "SELECT COUNT(*) AS n FROM tensions t "
                    "JOIN contracts c ON t.contract_id = c.id "
                    "JOIN code_points cp ON c.callee_id = cp.id "
                    "WHERE t.status = 'detected' AND cp.file_path LIKE ? ESCAPE '\\'",
                    like,
                ).fetchone()
                tensions = int(hit["n"] or 0)
        finally:
            conn.close()
        if not files:
            return CubeState(
                known=True, indexed=False, db=shown,
                detail=f"{shown} holds no file under this repo — not indexed here",
            )
        if reindexed:
            measure = f"{tensions} open tension(s)"
        else:
            measure = "tensions never measured — no reindex recorded"
        return CubeState(
            known=True, indexed=True, files=files, last_indexed_at=latest,
            open_tensions=tensions, reindexed=reindexed, db=shown,
            detail=(
                f"delta-cube holds {files} file(s) of this repo "
                f"(last indexed {latest or 'unknown'}); {measure}"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a state, never an exception
        return CubeState(
            known=False, db=shown,
            detail=f"could not read {shown}: {type(exc).__name__}: {exc}",
        )


@dataclass(frozen=True)
class LedgerState:
    """Whether tasky's ledger holds earlier sessions of this repo.

    Machine-wide, like the cube: one ledger holds every repository its owner
    worked in, and a session's absolute `cwd` is what places it, so a read is
    scoped by this repo's path. tasky itself groups a repository's clones and
    worktrees by their git remote; a path prefix cannot, so a second checkout
    reads as a repo with no history until a session runs there. Nothing here
    says whether the tasky tools are connected to the running session — a
    database cannot know that — so a consumer says "if the tools are
    connected", and never "call this".
    """

    #: True when the question was answered either way.
    known: bool = False
    #: The ledger file exists: tasky is set up on this machine.
    installed: bool = False
    #: Sessions whose working directory is this repo or under it.
    sessions: int = 0
    #: Problems recorded under this repo still in state `open`.
    open_problems: int = 0
    #: Date the oldest of those sessions started, "" when there are none.
    since: str = ""
    #: Which database was read, for a message a person acts on.
    db: str = ""
    detail: str = "not checked"

    @property
    def present(self) -> bool:
        """Is there anything of this repo to recall? Earlier sessions only."""
        return self.sessions > 0


def tasky_db() -> Path:
    """The ledger tasky writes on this machine, resolved like its `config.py`."""
    home = os.environ.get(TASKY_HOME_ENV)
    if home and home.strip():
        return Path(home).expanduser().absolute() / TASKY_DB_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = xdg if xdg and xdg.strip() else TASKY_DEFAULT_DATA_HOME
    return Path(base).expanduser() / TASKY_XDG_SUBDIR / TASKY_DB_NAME


def ledger_state(project: Path | str) -> LedgerState:
    """Does tasky hold earlier sessions of this repo? Read, never asked.

    Two counts and a date, nothing more: the messages themselves are what the
    tools are for, and reading them here would couple vise to a schema it does
    not own for a fact no hook needs.
    """
    db = tasky_db()
    shown = db.as_posix().replace(str(Path.home()), "~", 1)
    try:
        root = Path(project).resolve().as_posix().rstrip("/")
        if not db.is_file():
            return LedgerState(
                known=True, db=shown,
                detail=f"no {shown} — tasky is not set up on this machine",
            )
        conn = _connect(db)
        try:
            if not _has_table(conn, "sessions"):
                return LedgerState(
                    known=True, installed=True, db=shown,
                    detail=f"{shown} exists but has no sessions table",
                )
            under = (root, root.replace("%", "\\%").replace("_", "\\_") + "/%")
            row = conn.execute(
                "SELECT COUNT(*) AS n, MIN(started_at) AS since FROM sessions "
                "WHERE cwd = ? OR cwd LIKE ? ESCAPE '\\'",
                under,
            ).fetchone()
            sessions = int(row["n"] or 0)
            since = str(row["since"] or "")[:10]
            problems = 0
            if sessions and _has_table(conn, "problems"):
                hit = conn.execute(
                    "SELECT COUNT(*) AS n FROM problems "
                    "WHERE state = 'open' AND (cwd = ? OR cwd LIKE ? ESCAPE '\\')",
                    under,
                ).fetchone()
                problems = int(hit["n"] or 0)
        finally:
            conn.close()
        if not sessions:
            return LedgerState(
                known=True, installed=True, db=shown,
                detail=f"tasky ledger at {shown} holds no session of this repo yet",
            )
        return LedgerState(
            known=True, installed=True, sessions=sessions, open_problems=problems,
            since=since, db=shown,
            detail=(
                f"tasky ledger at {shown} holds {sessions} session(s) of this repo "
                f"since {since or 'unknown'}; {problems} open problem(s)"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a state, never an exception
        return LedgerState(
            known=False, db=shown,
            detail=f"could not read {shown}: {type(exc).__name__}: {exc}",
        )


def summary(project: Path | str) -> str:
    """One line per neighbour. What `/vise:status` and bootstrap report."""
    project = Path(project)
    lines = [f"livespec:   {index_state(project).detail}"]
    trace = trace_state(project)
    lines.append(f"flowtrace:  {trace.detail}")
    graph = graph_state(project)
    lines.append(f"Graphify:   {graph.detail}")
    lines.append(f"delta-cube: {cube_state(project).detail}")
    lines.append(f"tasky:      {ledger_state(project).detail}")
    return "\n".join(lines)


__all__ = [
    "FLOWTRACE_DIR",
    "GRAPHIFY_GRAPH",
    "LIVESPEC_DB",
    "CubeState",
    "GraphState",
    "IndexState",
    "LedgerState",
    "TraceState",
    "cube_state",
    "delta_cube_db",
    "error_signature",
    "graph_state",
    "index_state",
    "ledger_state",
    "summary",
    "tasky_db",
    "trace_state",
]
