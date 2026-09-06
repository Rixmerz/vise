# Design

## One sentence, three fields

The three mechanisms answer one question — who decides the shape of a run —
and they answer it the same way: the data decides, and code applies the
decision. They are fields on `Task` rather than a structure beside it, for the
reason `docs/agent-runtime.md` gives for every runtime field: a second task
type with its own ids and edges is a second workflow engine, and the two
disagree within a release. A task that declares none of them is the task it
was yesterday.

## Expansion is a task that is ready twice

The expanding task is never dispatched to a worker. It reaches readiness the
first time when its source succeeds, and the scheduler expands it: reads the
items, creates the children, and rewrites its own dependencies to be the
children's ids. It reaches readiness the second time when every child has
succeeded, and the scheduler completes it in-process as a join, writing a
`collection` artifact and spending nothing.

No new task state, and no machinery for "waiting on children". The existing
rule that a task whose dependency failed is blocked with the dependency named
is exactly the rule a join needs, and `_block_stalled` already writes
"blocked on gather[3], gather[7]". A `JOINING` state would have been a second
copy of the dependency logic, and the dependency logic is the most tested
thing in the loop.

Why the children are not created at plan time: the list does not exist yet.
The plan shows the template once and says so.

## Children are ordinary tasks

Each child is a `Task` copied from the template: id `gather[3]`, name with
`[3/12]`, `{item}` substituted into the prompt, the acceptance criteria and
the ownership patterns, and the item placed in the brief's context whether or
not the prompt used the placeholder. Nothing else about it is new. Routing,
admission, the honesty gates, the verifier, the ladder, the replanner,
integration and rollback apply unchanged, because the child *is* a task.

One consequence had to be fixed rather than noted: the replanner used to be
handed the YAML's task list. After an expansion that list has no children, and
a replan would have cancelled every one of them as "dropped by a replan". It is
now handed the live list.

`[n]` rather than `::n`: the `::` suffixes mark a task's *auxiliary* agents —
its verifier, its debugger, its re-specifier. A child is not auxiliary to the
template; it is the work. The index is 1-based and stable, in the order of the
source's list. `ArtifactStore` slugs `[3]` to `-3-`, so `gather[3]` and
`gather[13]` file apart and neither collides with a task named `gather-3`.

## Where the items come from

From an artifact, never from prose. The source worker emits
`{"kind": "plan", "payload": {"sub_questions": [...]}}` in its result fence,
the scheduler stores it, and expansion reads `payload[items]` from the first
of the source's artifacts that carries the key. Items may be strings or
objects: `{item}` renders a string as itself and an object as JSON;
`{item.key}` reaches into an object. Anything else in braces is left alone,
so a prompt that quotes a code block is not mangled by a formatter.

Three outcomes, kept distinct — the lesson `decouple.skipped` already records:
`found: 0` and "could not look" are different facts and must not render alike.

| The source's artifacts…                     | The join                                             |
|---------------------------------------------|------------------------------------------------------|
| carry the key with items                    | expands; `expanded` names the count                  |
| carry the key, empty                        | succeeds with zero children and says "0 item(s)"     |
| do not carry the key, or there is no store  | is `BLOCKED`; the reason names the source, the key, and what the source did carry |

## The cap is reported, never silent

`max_items` defaults to `DEFAULT_MAX_ITEMS = 25`. Not 250: a child is a
`claude -p` session on this machine, not a VM on someone's fleet, and a
default that could spend 250× one task's estimate on one list is a default
nobody would defend after the bill. A list longer than the cap is cut to it,
`expansion_truncated` carries how many were dropped, and the collection
artifact carries the same number. "No silent caps" is the Workflow skill's
rule, and it holds here.

## Why a failed child fails the join

The proposal says why. The design adds one thing: the join's reason names
every failed child, so `compose` can tell the next plan which items to redo
rather than "the gather failed".

## Resume re-expands from the recorded items

Children are not in the YAML. The state records, per template, the items that
became children and the children's ids; `resume` derives the children again
from the template and those items before it resets anything, so the loop has
tasks to dispatch whose records already exist — succeeded ones kept, the rest
reset, as for any task. Derived from the record and not re-read from the
store, because a resume that re-read the artifact could derive a different
width than the run it continues, and the ledger already names the children it
paid for. The property to test is that two expansions of one list produce
identical ids. Without an artifact store expansion cannot happen at all, and
the template blocks saying so; the CLI always supplies one.

## What the plan can say

The planner has no items. It shows the expanding task once, marked with its
source and its cap, and prices it as a range: one child's estimate to
`max_items` times it. `estimated_cost_usd` stays the floor, so the "spend
roughly" line under `run` is never an overstatement; the ceiling is rendered
beside it, and `notes` says when the ceiling would not fit what is left of
the budget. A note, not a problem, by the same rule as over-declared
parallelism: the plan is correct and will run, and the run stops for a person
when admission refuses, which is the designed behaviour. The concurrency
ceiling counts the template once, and the render says the width is decided at
run time.

## Convergence: rounds are not attempts

An attempt is a try at a task. A round is a *passing* try that may not have
been the last. `until.key` names the payload list; the scheduler keeps `seen`
— the string form of every item found so far, persisted in the state so it
survives a resume — and after a passing round computes what is new. Nothing
new: `stable` increments. Something new: `stable` resets and `seen` grows.
The task stops when `stable` reaches `stable_for` or the round count reaches
`max_rounds`; otherwise it goes back to `PENDING`, and its next brief carries
"already found — do not report again" with the list. Each round is admitted by
the ledger like any dispatch, and the verifier, where one applies, judges each
round.

The dedup is code because an agent asked "is this new" says yes. The round's
verdict must be `pass` because a failed round is a failed attempt and takes
the ladder; folding "found nothing" into "failed" is the conflation of
`INCONCLUSIVE` with `FAIL` that `Verdict` exists to prevent.

## The panel: lenses, then a majority

`N` briefs, one per lens, drawn in order from a fixed tuple — the criteria as
written; whether the quoted evidence reproduces; what an adversary would try —
cycling when `N` exceeds the tuple. Each is today's verifier brief with the
lens's instruction added, and none sees another's answer. The decision is
code: `pass` on a majority of passes; `fail` on a majority of fails, with the
union of the reasons; otherwise `BLOCKED`, because verifiers who could not
decide have not decided. Every verdict is stored as its own `verification`
artifact under `task::verify[k]`, with the lens in the payload, and the ledger
charges each. The plan prices a panel as `N` times one verifier.

A majority rather than unanimity because the lenses differ on purpose: a
verifier looking at reproducibility may have nothing to say about a criterion
that is about wording, and unanimity would let the lens least able to evaluate
veto the ones that could.

## Checked and found not to be there

- No node-level `enabled` exists, so nothing here can ship disabled. Every
  field defaults off instead, which is the stronger property.
- `capability_hint` splits an id on non-word characters; `gather[3]` yields
  `gather` and `3`, neither a capability word, so a child routes as its
  template does. Pinned by a test.
- A run never spans nodes, so a `for_each` source is a task in the same node.
  The research graph's `scope` phase runs in the session before the run
  starts, and its output is not in the store; `gather` therefore gets a
  `split` task of its own, and the phase keeps its job for the session.
