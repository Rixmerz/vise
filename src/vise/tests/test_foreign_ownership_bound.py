"""How wide rule 4's excuse actually is, asserted rather than described.

`honesty.check_result` excuses a write into a peer's declared territory, because
in a shared working tree a git diff cannot say whose file is whose. Its comment
used to bound that excuse to "for exactly as long as that peer is in flight".

The scheduler passes every non-conflicting writing task in the node, whatever
state it is in, and its own docstring explains why it has to: two peers
dispatched in the same pass each miss the other, so an in-flight snapshot
refuses both writers for each other's work.

Both statements were in the repo, one file apart, and only one was true. This
pins the behaviour so the prose has something to be wrong about.
"""
from __future__ import annotations

from vise.engines.graph_engine import Task
from vise.runtime.registry import AgentRegistry, AgentSpec
from vise.runtime.scheduler import Scheduler
from vise.runtime.worker import MockWorker


def _scheduler() -> Scheduler:
    reg = AgentRegistry()
    reg.agents["backend-python"] = AgentSpec(
        id="backend-python", role="backend", description="d", model="sonnet",
        capabilities=("backend", "python"))
    return Scheduler(worker=MockWorker(), registry=reg)


def _tasks():
    return {
        "a": Task(id="a", name="A", role="backend", ownership=["src/a/**"]),
        "b": Task(id="b", name="B", role="backend", ownership=["src/b/**"]),
        "c": Task(id="c", name="C", role="backend", ownership=["src/c/**"]),
        "reader": Task(id="reader", name="R", role="review",
                       ownership=["src/d/**"], writes=False),
    }


def test_the_excuse_covers_peers_that_are_not_running_yet():
    """The claim the old comment made, and the behaviour that contradicts it."""
    claims = _scheduler()._foreign_ownership(_tasks(), "a")

    assert "src/b/**" in claims and "src/c/**" in claims, (
        "peers that have not been dispatched are excused too — which is what "
        "makes the excuse node-wide rather than in-flight"
    )


def test_a_read_only_peer_is_not_excused():
    """A task that declares no writes cannot be the one who wrote the file."""
    assert "src/d/**" not in _scheduler()._foreign_ownership(_tasks(), "a")


def test_a_conflicting_peer_is_not_excused():
    """Admission serialises them, so their files appearing mid-run is not
    a peer being busy — it is something genuinely wrong."""
    tasks = _tasks()
    tasks["b"] = Task(id="b", name="B", role="backend", ownership=["src/a/**"])

    assert "src/a/**" not in _scheduler()._foreign_ownership(tasks, "a")


def test_isolation_passes_no_foreign_ownership_at_all():
    """The fix that removes the cost instead of widening the excuse.

    Under `--isolate` a task's tree holds only its own writes, so the gate goes
    back to being strict — and the comment in `honesty` now says so.
    """
    import inspect

    src = inspect.getsource(Scheduler.run) + inspect.getsource(
        Scheduler._dispatch_ready)
    assert "() if self._pool is not None" in src, (
        "isolation must pass an empty foreign-ownership set, or the excuse "
        "survives the thing that was supposed to remove it"
    )


def test_the_honesty_comment_does_not_claim_an_in_flight_bound():
    """The specific false sentence, in the file that carried it."""
    import inspect

    from vise.runtime import honesty

    text = inspect.getsource(honesty)
    assert "for exactly as long as that peer is in flight" not in text, (
        "the excuse is node-wide and lasts the whole run; saying otherwise "
        "understates how much rule 4 gives up in a shared tree"
    )
