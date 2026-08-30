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

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GIT_TIMEOUT_S = 60


@dataclass(frozen=True)
class GitResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""


def _git(cwd: Path | str, *args: str, timeout: int = GIT_TIMEOUT_S) -> GitResult:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GitResult(False, "", str(exc))
    return GitResult(proc.returncode == 0, proc.stdout, proc.stderr)


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
        for task_id in list(self.worktrees):
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
            return IntegrationResult(task_id, True, (), (), "the task changed nothing")

        changed = self.changed_paths(task_id)
        patch = self.root / "patches" / f"{_slug(task_id)}.patch"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(diff.stdout, encoding="utf-8")

        # Snapshot exactly the paths this patch touches, so a failure can be
        # backed out without touching anything else. `git checkout -- .` would
        # be shorter and would also discard every other task's integrated work
        # and any edit the user had in flight.
        snapshot = self._snapshot(changed)
        applied = _git(self.project_dir, "apply", "--3way", "--whitespace=nowarn", str(patch))
        if applied.ok:
            # Kept so the next worktree starts from what this run has built, not
            # from a HEAD that integration never moves.
            self.integrated.append(patch)
            return IntegrationResult(task_id, True, changed, (), "applied cleanly")

        conflicts = _conflicted_paths(self.project_dir)
        # --3way writes conflict markers into the tree and unmerged entries into
        # the index. A half-applied patch is the one state nobody can reason
        # about, and the caller is being told to decide, not handed a mess.
        self._restore(snapshot)
        if changed:
            _git(self.project_dir, "reset", "-q", "--", *changed)
        return IntegrationResult(
            task_id, False, changed, conflicts,
            reason=(
                f"the patch does not apply: {applied.stderr.strip()[:400]}"
                if not conflicts else
                "two tasks changed the same lines — either an ownership "
                "declaration was wrong or the plan was"
            ),
        )


    # --- backing out ------------------------------------------------------

    def _snapshot(self, paths: tuple[str, ...]) -> dict[str, bytes | None]:
        """The current bytes of each path, or None where it does not exist."""
        out: dict[str, bytes | None] = {}
        for rel in paths:
            target = self.project_dir / rel
            try:
                out[rel] = target.read_bytes() if target.is_file() else None
            except OSError:
                out[rel] = None
        return out

    def _restore(self, snapshot: dict[str, bytes | None]) -> None:
        for rel, data in snapshot.items():
            target = self.project_dir / rel
            try:
                if data is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            except OSError:
                continue


def _conflicted_paths(project_dir: Path) -> tuple[str, ...]:
    listed = _git(project_dir, "diff", "--name-only", "--diff-filter=U")
    if not listed.ok:
        return ()
    return tuple(p for p in listed.stdout.splitlines() if p.strip())


def _slug(value: str) -> str:
    """A task id as a filesystem and git-refname-safe component.

    ``.`` is not in the allowed set. A task id containing ``..`` would otherwise
    survive into a branch name, which git rejects outright — and into a path,
    where it would mean something else entirely.
    """
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.strip())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "task"
