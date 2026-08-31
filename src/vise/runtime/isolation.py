"""One working tree per task — the fix that removes the attribution problem
rather than bounding it.

With every task writing into one shared tree, a git diff cannot say whose file
is whose. The ownership gate copes by excusing paths a concurrent peer was
entitled to write (``honesty.check_result``), which is correct and bounded and
still an excuse. Give each task its own worktree and the question disappears:
the only writes in that tree are that task's.

The mechanics are ordinary git worktrees, which share the main repository's
object database. That sharing is what makes integration cheap — a blob written
in a worktree is already present in the main repo, so a three-way apply has the
bases it needs without a fetch.

**Integration reports conflicts; it does not resolve them.** A conflict means
two tasks changed the same lines, which is either an ownership declaration that
was wrong or a plan that was. Both are decisions, and a runtime that picks a
side silently is making them on nobody's behalf.

Every operation is opt-in and every failure is reported rather than raised into
the scheduler loop: a repository that cannot host worktrees (no git, a bare
repo, a filesystem that refuses links) must degrade to the shared-tree
behaviour, not take the run down.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

GIT_TIMEOUT_S = 60


@dataclass(frozen=True)
class GitResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    #: git's stdout unchanged. A patch has to be written from these, not from
    #: `stdout` re-encoded — see `_git`.
    stdout_bytes: bytes = b""


def _git(
    cwd: Path | str, *args: str, timeout: int = GIT_TIMEOUT_S,
    env: dict[str, str] | None = None,
) -> GitResult:
    """Run one git command. Reports; never raises.

    Output is captured as bytes and decoded with ``surrogateescape`` rather than
    with ``text=True``, which decodes strict. git base64-encodes a diff only for
    files it considers *binary* — meaning NUL-containing — so a latin-1 source
    file is diffed as text and its raw bytes reach the decoder. The resulting
    ``UnicodeDecodeError`` is a ``ValueError``, so it passed straight through the
    old ``except`` and out of ``Scheduler.run``, skipping ``cleanup()`` and
    stranding a worktree and a branch. ``stdout_bytes`` is kept beside the text
    because re-encoding a surrogateescape'd diff as strict UTF-8 corrupts the
    patch — the writer wants the bytes git produced, unchanged.

    The ``except`` is broad for the same reason: a helper whose contract is
    "report, never raise" cannot have a list of failures it makes an exception
    for.
    """
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, timeout=timeout, env=env
        )
    except Exception as exc:  # noqa: BLE001 - the contract is report, never raise
        return GitResult(False, "", str(exc))
    return GitResult(
        proc.returncode == 0,
        proc.stdout.decode("utf-8", "surrogateescape"),
        proc.stderr.decode("utf-8", "surrogateescape"),
        proc.stdout,
    )


@dataclass(frozen=True)
class _Entry:
    """What one path was, before an integration that might be backed out.

    ``kind`` is "file", "symlink", "absent" or "other"; ``data`` is the bytes or
    the link target; ``mode`` is the permission bits of a regular file.
    """

    kind: str
    data: bytes
    mode: int


@dataclass(frozen=True)
class IntegrationResult:
    """What came back from one task's worktree."""

    task_id: str
    applied: bool
    changed_paths: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    reason: str = ""

    def __bool__(self) -> bool:
        return self.applied


class IsolationUnavailable(Exception):
    """Raised by ``WorktreePool.create`` when the repo cannot host worktrees.

    A constructor-time failure, deliberately: a caller that asked for isolation
    and cannot have it should find out before a run starts, not when the first
    task tries to write. The scheduler catches it and falls back to the shared
    tree, saying so.
    """


@dataclass
class WorktreePool:
    """A worktree per task, and the integration that brings each one back."""

    project_dir: Path
    root: Path
    branch_prefix: str = "vise/run"
    run_id: str = "run"
    worktrees: dict[str, Path] = field(default_factory=dict)
    #: Every patch this run has integrated into the main tree, in the order it
    #: landed. Replayed into each new worktree — see ``_seed``.
    integrated: list[Path] = field(default_factory=list)

    @classmethod
    def create(cls, project_dir: Path | str, root: Path | str, run_id: str) -> WorktreePool:
        """Build a pool, or refuse with a reason the caller can report."""
        project = Path(project_dir).resolve()
        if not shutil.which("git"):
            raise IsolationUnavailable("git is not on PATH")
        head = _git(project, "rev-parse", "HEAD")
        if not head.ok:
            raise IsolationUnavailable(
                f"{project} is not a git repository with a commit "
                f"({head.stderr.strip() or 'no HEAD'})"
            )
        return cls(project_dir=project, root=Path(root).resolve(), run_id=run_id)

    # --- lifecycle -------------------------------------------------------

    def branch_for(self, task_id: str) -> str:
        return f"{self.branch_prefix}/{self.run_id}/{_slug(task_id)}"

    def acquire(self, task_id: str) -> Path:
        """The worktree for one task, created on first use.

        Branched from the main tree's HEAD and then seeded with everything this
        run has already integrated — never from another task's branch. Chaining
        branches would make the order tasks happened to start in part of the
        result; replaying the integrated set does not, because that set is by
        construction what had finished and verified before this task began,
        which is exactly what the dependency edges promised it.

        Branching from bare HEAD, which is what this did first, is what makes a
        DAG with dependencies unbuildable under isolation: integration writes to
        the main *working tree* without committing, so HEAD never moves and a
        fresh worktree starts without a single thing the run has produced. A
        task whose dependencies have all landed was handed a tree that did not
        contain them. That is not isolation, it is amnesia.
        """
        existing = self.worktrees.get(task_id)
        if existing is not None:
            return existing
        path = self.root / "worktrees" / _slug(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self._remove(path)
        branch = self.branch_for(task_id)
        _git(self.project_dir, "worktree", "prune")
        _git(self.project_dir, "branch", "-D", branch)
        result = _git(self.project_dir, "worktree", "add", "-b", branch, str(path), "HEAD")
        if not result.ok:
            raise IsolationUnavailable(
                f"could not create a worktree for {task_id}: {result.stderr.strip()}"
            )
        self.worktrees[task_id] = path
        self._seed(task_id, path)
        return path

    def _seed(self, task_id: str, path: Path) -> None:
        """Replay this run's integrated work into a fresh worktree, and commit it.

        The commit is what keeps the task's own diff its own. Left uncommitted,
        the seeded files would show up in ``git diff HEAD`` as this task's
        writes — re-integrated under its name, and refused by the ownership gate
        for writing outside what it declared. The commit lands on the throwaway
        ``vise/run/<id>/<task>`` branch, which ``release`` deletes; nothing here
        ever writes to a branch the user has.

        A replay that will not apply raises ``IsolationUnavailable``, and the
        scheduler's answer to that is to run the task in the shared tree — which
        is precisely the tree that already holds the work this failed to copy.
        """
        if not self.integrated:
            return
        for patch in self.integrated:
            applied = _git(path, "apply", "--3way", "--whitespace=nowarn", str(patch))
            if not applied.ok:
                self._remove(path)
                self.worktrees.pop(task_id, None)
                _git(self.project_dir, "branch", "-D", self.branch_for(task_id))
                raise IsolationUnavailable(
                    f"could not replay already-integrated work into {task_id}'s "
                    f"worktree ({patch.name}): {applied.stderr.strip() or 'apply failed'}"
                )
        _git(path, "add", "-A")
        # An explicit identity: the repo under test may have none configured,
        # and a commit that fails for that would leave the seeded work looking
        # like this task wrote it.
        committed = _git(
            path,
            "-c", "user.name=vise", "-c", "user.email=vise@localhost",
            "commit", "--no-verify", "-m",
            f"vise: work integrated before {task_id} started",
        )
        if not committed.ok and "nothing to commit" not in committed.stdout.lower():
            self._remove(path)
            self.worktrees.pop(task_id, None)
            _git(self.project_dir, "branch", "-D", self.branch_for(task_id))
            raise IsolationUnavailable(
                f"could not commit the replayed work in {task_id}'s worktree: "
                f"{committed.stderr.strip() or committed.stdout.strip()}"
            )

    def release(self, task_id: str) -> None:
        """Drop one task's worktree and its branch. Safe to call twice."""
        path = self.worktrees.pop(task_id, None)
        if path is not None:
            self._remove(path)
        _git(self.project_dir, "branch", "-D", self.branch_for(task_id))

    def cleanup(self) -> None:
        """Drop every worktree, keeping whatever work never made it out."""
        for task_id in list(self.worktrees):
            self.preserve(task_id)
            self.release(task_id)
        _git(self.project_dir, "worktree", "prune")

    def _remove(self, path: Path) -> None:
        _git(self.project_dir, "worktree", "remove", "--force", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    # --- integration -----------------------------------------------------

    def changed_paths(self, task_id: str) -> tuple[str, ...]:
        """Everything this task's worktree changed, relative to its own HEAD."""
        path = self.worktrees.get(task_id)
        if path is None:
            return ()
        _git(path, "add", "-A")
        listed = _git(path, "diff", "--cached", "--name-only", "HEAD")
        if not listed.ok:
            return ()
        return tuple(p for p in listed.stdout.splitlines() if p.strip())

    def integrate(self, task_id: str) -> IntegrationResult:
        """Apply one task's changes to the main working tree.

        Three-way apply, so a genuine textual conflict is reported as one rather
        than silently clobbering. On any failure the main tree is left exactly as
        it was: ``git apply`` is atomic per invocation, and nothing here writes
        outside it.
        """
        path = self.worktrees.get(task_id)
        if path is None:
            return IntegrationResult(task_id, False, reason="no worktree for this task")

        _git(path, "add", "-A")
        diff = _git(path, "diff", "--cached", "--binary", "HEAD")
        if not diff.ok:
            return IntegrationResult(
                task_id, False, reason=f"could not read the worktree diff: {diff.stderr.strip()}"
            )
        if not diff.stdout.strip():
            # `git add -A` honours .gitignore, so a task whose whole output is
            # an ignored path produces an empty diff. Reporting that as "changed
            # nothing" made the scheduler release the worktree, which
            # force-removes it: a green run, and the work gone. Only checked on
            # this branch, so a task that also edited tracked files — and may
            # have run a build on the way — is unaffected.
            ignored = self.ignored_writes(task_id)
            if ignored:
                return IntegrationResult(
                    task_id, False, ignored, (),
                    reason=(
                        "the task wrote only paths this repository ignores, so "
                        "there is no diff to integrate: "
                        + ", ".join(ignored[:20])
                    ),
                )
            return IntegrationResult(task_id, True, (), (), "the task changed nothing")

        changed = self.changed_paths(task_id)
        patch = self.root / "patches" / f"{_slug(task_id)}.patch"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_bytes(diff.stdout_bytes)

        # Snapshot exactly the paths this patch touches, so a failure can be
        # backed out without touching anything else. `git checkout -- .` would
        # be shorter and would also discard every other task's integrated work
        # and any edit the user had in flight.
        snapshot = self._snapshot(changed)
        applied, conflicts = self._apply(patch, changed)
        if applied.ok:
            # Kept so the next worktree starts from what this run has built, not
            # from a HEAD that integration never moves.
            self.integrated.append(patch)
            return IntegrationResult(task_id, True, changed, (), "applied cleanly")

        # --3way writes conflict markers into the tree and unmerged entries into
        # the index. A half-applied patch is the one state nobody can reason
        # about, and the caller is being told to decide, not handed a mess.
        self._restore(snapshot)
        return IntegrationResult(
            task_id, False, changed, conflicts,
            reason=(
                f"the patch does not apply: {applied.stderr.strip()[:400]}"
                if not conflicts else
                "two tasks changed the same lines — either an ownership "
                "declaration was wrong or the plan was"
            ),
        )


    def _apply(
        self, patch: Path, changed: tuple[str, ...]
    ) -> tuple[GitResult, tuple[str, ...]]:
        """Three-way apply the patch, without touching the user's index.

        `git apply --3way` implies `--index`, and git refuses before attempting
        any merge when the index and the working tree disagree. So any
        uncommitted human edit to a file the task touched came back as
        "does not match index" and was reported as a conflict — blaming an
        ownership declaration for the user's own work in progress, which is the
        normal state of a repository someone is working in.

        The apply therefore runs against a *scratch* index seeded from the real
        one with those paths staged, so index and worktree agree by
        construction. Two things follow. The user's staging state is left
        exactly as it was, since nothing here writes their index. And a
        successful integration no longer leaves its paths staged — which is
        what made backing out a *later* failure with `git reset -- <paths>`
        reset already-integrated entries to HEAD, leaving index and worktree
        disagreeing and every subsequent task on those files refused.
        """
        with tempfile.TemporaryDirectory() as scratch:
            index = Path(scratch) / "index"
            located = _git(
                self.project_dir, "rev-parse", "--path-format=absolute",
                "--git-path", "index",
            )
            real = Path(located.stdout.strip()) if located.ok and located.stdout.strip() else None
            if real is not None and real.is_file():
                try:
                    shutil.copyfile(real, index)
                except OSError:
                    pass
            env = {**os.environ, "GIT_INDEX_FILE": str(index)}
            if changed:
                _git(self.project_dir, "add", "-A", "--", *changed, env=env)
            applied = _git(
                self.project_dir, "apply", "--3way", "--whitespace=nowarn",
                str(patch), env=env,
            )
            # Read the unmerged entries here, while the scratch index still
            # exists. `--3way` records them there, not in the real index, so
            # asking afterwards would report every conflict as none.
            conflicts = _conflicted_paths(self.project_dir, env=env)
        return applied, conflicts

    def ignored_writes(self, task_id: str) -> tuple[str, ...]:
        """Paths this task wrote that the repository's .gitignore excludes.

        A worktree is a fresh checkout, so anything ignored and present in it
        was produced after the task started.
        """
        path = self.worktrees.get(task_id)
        if path is None:
            return ()
        listed = _git(path, "ls-files", "--others", "--ignored", "--exclude-standard")
        if not listed.ok:
            return ()
        return tuple(p for p in listed.stdout.splitlines() if p.strip())

    def preserve(self, task_id: str) -> Path | None:
        """Write a task's uncollected work to a patch, or None if there is none.

        A patch is otherwise only written inside `integrate`, which a cancelled
        or stopped task never reaches — so `cleanup` force-removed worktrees
        holding real work with no record of it anywhere. That is the same money
        the ledger reports having spent.
        """
        path = self.worktrees.get(task_id)
        if path is None:
            return None
        _git(path, "add", "-A")
        diff = _git(path, "diff", "--cached", "--binary", "HEAD")
        ignored = self.ignored_writes(task_id)
        if not diff.ok or (not diff.stdout.strip() and not ignored):
            return None
        out = self.root / "patches" / f"{_slug(task_id)}.unintegrated.patch"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            body = diff.stdout_bytes
            if ignored:
                body += (
                    "\n# vise: this task also wrote paths the repository ignores, "
                    "which no diff can carry:\n"
                    + "".join(f"#   {p}\n" for p in ignored)
                ).encode("utf-8")
            out.write_bytes(body)
        except OSError:
            return None
        return out

    # --- backing out ------------------------------------------------------

    def _snapshot(self, paths: tuple[str, ...]) -> dict[str, _Entry]:
        """What each path *is* right now — kind, contents and mode.

        ``lstat``, not ``is_file()``. The old version followed symlinks, so a
        link pointing at something not yet generated answered "does not exist"
        and the restore honoured that by deleting it; a live link had its
        *target's* bytes captured, which the restore then wrote back through the
        link into the target. Both lose a file the patch was refused for
        touching, from the path whose docstring promises otherwise.
        """
        out: dict[str, _Entry] = {}
        for rel in paths:
            target = self.project_dir / rel
            try:
                st = os.lstat(target)
            except OSError:
                out[rel] = _Entry("absent", b"", 0)
                continue
            try:
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = _Entry(
                        "symlink", os.readlink(target).encode("utf-8", "surrogateescape"), 0
                    )
                elif stat.S_ISREG(st.st_mode):
                    out[rel] = _Entry("file", target.read_bytes(), stat.S_IMODE(st.st_mode))
                else:
                    # A directory, fifo or device where a file was expected is
                    # not something to restore by writing bytes over it.
                    out[rel] = _Entry("other", b"", 0)
            except OSError:
                out[rel] = _Entry("other", b"", 0)
        return out

    def _restore(self, snapshot: dict[str, _Entry]) -> None:
        for rel, entry in snapshot.items():
            target = self.project_dir / rel
            if entry.kind == "other":
                continue
            try:
                # Unlink first in every case: writing bytes to a path that is
                # now a symlink writes through it into whatever it points at.
                if target.is_symlink() or target.exists():
                    target.unlink()
            except OSError:
                continue
            try:
                if entry.kind == "absent":
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if entry.kind == "symlink":
                    os.symlink(entry.data.decode("utf-8", "surrogateescape"), target)
                else:
                    target.write_bytes(entry.data)
                    if entry.mode:
                        os.chmod(target, entry.mode)
            except OSError:
                continue


def _conflicted_paths(
    project_dir: Path, *, env: dict[str, str] | None = None
) -> tuple[str, ...]:
    listed = _git(project_dir, "diff", "--name-only", "--diff-filter=U", env=env)
    if not listed.ok:
        return ()
    return tuple(p for p in listed.stdout.splitlines() if p.strip())


def _slug(value: str) -> str:
    """A task id as a filesystem and git-refname-safe component.

    ``.`` is not in the allowed set. A task id containing ``..`` would otherwise
    survive into a branch name, which git rejects outright — and into a path,
    where it would mean something else entirely.

    Injective, via a suffix of the raw id's hash. Without it ``api.v2`` and
    ``api/v2`` both became ``api-v2`` and therefore shared one worktree path,
    one branch and one patch file — and ``acquire`` force-removes whatever is
    already at the path, so the second task destroyed the first one's tree
    while it was still running.
    """
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.strip())
    while "--" in out:
        out = out.replace("--", "-")
    out = out.strip("-")[:40].strip("-") or "task"
    return f"{out}-{hashlib.sha1(value.encode('utf-8', 'surrogateescape')).hexdigest()[:8]}"
