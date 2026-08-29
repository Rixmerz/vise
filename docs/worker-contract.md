# The worker contract

A worker is whatever executes one task: a Claude Code subagent
(`adapters/claude_code.py`), a recording mock, in principle a shell script. The
contract is the same, and it is deliberately narrow: a worker receives a brief
and owes back a result. It does not read the graph, does not advance a phase,
and does not decide whether its own work was good enough.

Two things the adapter does not take the worker's word for. **Changed paths come
from git**, diffed across the run — the honesty gates check what a task wrote
against what it was allowed to write, and reading that list from the model's own
report would let the one party being checked supply the evidence. And **a reply
with no verdict block is `inconclusive`**, never a pass: any other reading makes
a model that ignored its instructions indistinguishable from one that
succeeded.

## The brief

```
TaskBrief
├── task            id, name, role, prompt, criticality
├── ownership       the paths this task may write, as globs
├── acceptance      the criteria this task is judged against
├── context         resolved files, symbols, decisions — not a transcript
├── inputs          artifacts produced by this task's dependencies
├── attempts        every previous attempt at this task, with its classification
├── restrictions    tools blocked, MCPs enabled
└── budget          cost ceiling, turn ceiling, timeout
```

Two of these carry most of the value, and both are about *what the worker does
not get*.

**`context`, not the conversation.** Handing each worker the whole session is the
default that makes multi-agent orchestration cost more than doing the work
serially, and it degrades quality on top: the relevant three files are somewhere
in there, competing with forty that are not. The context resolver assembles
files, symbols, prior findings, decisions, the diff so far, and relevant entries
from vise's experience memory — which already indexes learnings per file with
retrievability decay, so "this module had timezone-offset problems" reaches the
worker as one line instead of as a thread the worker has to re-read.

**`attempts`, so the second try is a different try.** Every prior attempt, its
model and effort, and how its failure was classified:

```
previous attempts on this task — already tried, do not repeat:
  attempt 1 [sonnet/medium, code_bug]  parser accepted '٥' as a digit
  attempt 2 [sonnet/high,  test_bug]   the added test asserted the wrong branch
```

An agent that cannot see the last agent's attempt repeats it, confidently and at
full price.

## The result

```
TaskResult
├── verdict         pass | fail | inconclusive
├── summary         one paragraph, for a human
├── evidence        commands run and their real output, verbatim
├── changed_paths   what was actually written
├── artifacts       structured output for downstream tasks
├── usage           tokens in/out, cost, wall time
└── classification  set on failure: code | test | spec | architecture | environment
```

`inconclusive` is a first-class verdict and not a synonym for `fail`. A worker
that could not run the test suite because the database was not up has not shown
the code is wrong, and recording that as a failure sends the next attempt to fix
code that may be fine.

## Honesty rules

These are **gates, and they fail closed.** A gate that cannot evaluate must not
report success — the inverse of vise's hook rule, where a handler that cannot run
must never take the session down. Each rule below refuses a result; none of them
judges content, because a runtime that grades prose is a runtime that can be
argued with.

1. **A pass from a testing role needs evidence.** The command and its real
   output, verbatim. Not "tests pass" — the text the terminal produced.
2. **A pass from an implementing role needs checks.** The repo's *existing*
   checks, run and quoted: the claim being made is "I did not break what was
   there". Writing new tests is a different role's job.
3. **A pass claiming edits needs a changed tree.** The worker enters with a
   baseline hash of `git status --porcelain` plus `HEAD`; a pass whose tree and
   HEAD are both unchanged is refused. HEAD is in the hash so that a worker which
   commits its work still moves it — otherwise committing looks identical to
   doing nothing and the gate wedges.
4. **A pass may not write outside its ownership.** A result whose
   `changed_paths` escape the declared globs is refused, and the escape is
   reported rather than trimmed.

Rules 1–3 are mini-vise's, ported. Rule 3 in particular is the only mechanical
honesty check in either codebase — every other one asks another model — and it
degrades open in exactly one direction: when either hash is unknowable (no
directory, git absent, not a repo, subprocess error) it does not block. A gate
that cannot compute its input has no finding, and inventing one would be the same
error it exists to catch.

Known and accepted: a worker that creates a file and deletes it again is refused
too. The alternative is a gate that models intent.

## The worker does not grade itself

`verdict` is the worker's claim. `SUCCEEDED` is the runtime's conclusion, and
reaching it takes a **verifier** — a different agent, given a different input:

```
verifier input:  acceptance criteria + task + diff + evidence
verifier output: { verdict, confidence, evidence }
```

The verifier never sees the worker's reasoning, only its output. That is the
whole point: a reviewer who reads the argument for why the code is right is
reviewing the argument.

Above the verifier sits an adversarial **reviewer**, which is not asked "is this
correct". It is asked to find reasons this should not ship, and its charter names
the probes: boundary conditions, malformed and non-ASCII input, concurrency,
timeouts and retries, partial failure, data corruption, permissions, API
compatibility. Naming them matters — the measured gap in the sweep behind
[`model-routing.md`](model-routing.md) was a reviewer that missed non-ASCII input
twice, on the largest model available, because nothing told it to look.

## Artifacts, not transcripts

Workers communicate through structured artifacts written to a store, addressed by
task id and kind: `research`, `plan`, `finding`, `test-report`, `verification`,
`review`. A downstream task receives the artifacts of its dependencies, not their
conversations.

This is a cost decision and a correctness decision at once. Thirty thousand
tokens of one worker's transcript is both expensive and a worse input than the
four hundred tokens of what it concluded, because the transcript contains every
hypothesis it abandoned with the same weight as the one it kept.

## What a worker never does

- Advance a phase, traverse an edge, or write graph state.
- Widen its own ownership, budget, or tool set.
- Report `pass` on work it could not verify. That is `inconclusive`, and it costs
  the run nothing to say so.

## Related

- [`agent-runtime.md`](agent-runtime.md) — the two planes and the boundary
- [`scheduler.md`](scheduler.md) — admission, retry, escalation, human gates
