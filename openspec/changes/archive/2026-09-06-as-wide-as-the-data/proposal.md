# As wide as the data

## Why

Three things were named as the line this project should follow: Manus's Wide
Research, which the press calls a swarm and Manus does not; Claude Code's
Workflow tool, where the shape of a run is a script; and ultracode, where every
substantial task gets a workflow and every finding is refuted before it is
believed. Put beside the runtime that exists, they are not three features. They
are one sentence the runtime does not yet say: **the shape of a run is decided
by what the run finds, not by what the author of the YAML could foresee.**

What each of the three actually does, and where vise stands against it.

**Width from the data.** Wide Research takes one request, has a main agent
split it into independent items, and gives every item a dedicated agent with a
fresh context window on its own machine; the outputs are structured and the
main agent assembles them. Tested to 250 items, and item 250 gets the depth
item 1 got, because nothing is shared. vise already has the per-item half, and
one thing Wide Research lacks: a worker is a full Claude Code session,
`--isolate` gives it a worktree of its own, a result is a structured artifact
rather than a transcript, and admission prices every dispatch against a ceiling
before it starts. What vise lacks is the width. A `dag` node's tasks are a list
written before anyone has seen the data. `research-graph.yaml` shows the gap
exactly: `scope` is told to split the question into sub-questions that can be
pursued independently, and `gather` then runs four fixed passes over the whole
question, because there is no way to write "one of these per sub-question".

**Shape as code.** The Workflow tool's premise is that control flow belongs in
a script and agents return data: `pipeline` and `parallel` are code, a loop
that runs until two rounds find nothing new is code, deduplication is code, and
a bound on coverage is logged rather than applied silently. vise agrees with
the premise — `workflow-gating` says "Prose cannot declare structure", the
runtime's event log is a journal, and a worker's result is a fenced JSON block.
But the one dynamic shape the runtime has is the replan, and it is for failure.
Discovery has no shape: a pass that should run until it stops finding — a
duplicate hunt, a test-gap sweep, the case against — is one task, run once. The
research graph's `verify → gather` back-edge is the closest thing, and it is
opened by a sentence the agent says and bounded by `max_visits`.

**Opinions in proportion to the cost of being wrong.** Ultracode's verify step
is adversarial and plural: several independent agents each try to refute a
finding, through different lenses where a finding can fail in more than one
way, and it dies on a majority. vise verifies every task that declares
acceptance criteria with exactly one agent. `criticality: critical` pins the
*model* to the top tier; it does not change how many opinions `SUCCEEDED`
needs. The most expensive work in a run is judged once.

## What Changes

Three fields on `Task`, all optional, all defaulting to today's behaviour, and
one bundled workflow that uses the first.

**`for_each` — one task per item of an upstream result.** A task may declare
that it expands: `from` names a task in the same node, `items` names a key in
that task's artifact payload, `max_items` caps the width. When the source
succeeds, the scheduler reads the list and creates one child task per item —
same role, same acceptance, same ownership with `{item}` substituted — as
ordinary tasks that route, price, verify, escalate and integrate like any
other. The declaring task becomes the join: it dispatches no worker, succeeds
when every child does, and hands downstream tasks a `collection` artifact
beside the children's own. A list the source did not produce blocks the join
with the reason; a list it produced empty succeeds with zero children and says
so; a list longer than the cap is cut and the cut is recorded.

**`until` — a task that runs until it stops finding.** A task may declare
`stable_for` and `max_rounds`. A round is one passing attempt; the scheduler
keys what the round found by a named payload list, counts what is new against
everything seen so far, and dispatches another round — briefed with what is
already found — until `stable_for` consecutive rounds add nothing or
`max_rounds` is reached. The dedup is code. A round that fails is a failure and
takes the ladder; only a pass is a round.

**`verifiers` — how many independent opinions `SUCCEEDED` needs.** A task may
declare a count. Each verifier is briefed as today, from a distinct lens — the
criteria as written, whether the evidence reproduces, what an adversary would
try — and none sees another. A majority of `pass` succeeds; a majority of
`fail` escalates with the union of the reasons; anything else blocks, because
verifiers who could not decide have not decided. Every verdict is recorded on
its own.

**`research-graph.yaml` gathers per sub-question.** A `split` task writes the
sub-questions into an artifact, and a `for_each` task answers each one on its
own agent. This is the first bundled workflow whose width the data decides,
and the reason the field exists rather than being described.

**The plan says what it cannot know.** `vise runtime plan` renders an
expanding task as a range — one to `max_items` children, priced per item — a
converging task as one to `max_rounds` rounds, and a panel as its count. It
notes when the widest case would not fit the budget rather than pretending
the run is bounded.

## What this deliberately does not do

**It does not add `run_start`.** A swarm here is dispatched by
`vise runtime run`, which a person types with a ceiling on it. Wide Research is
a product with its own fleet of machines; vise's version is a command with a
`--max-cost`, and the boundary `test_there_is_no_run_start_tool` pins is not
reopened for width.

**It does not become the Workflow tool.** That tool is Claude Code's, invoked
when the user opts in and never by a plugin. What is borrowed is the idea that
shape is code; the API is not.

**It does not let an agent decide the shape.** `for_each` reads a list a
worker produced, and that is the whole of the worker's say. The count, the cap,
the dedup key, the stop rule and the number of verifiers are declared in the
graph and executed by the scheduler. An agent that could set its own verifier
count would set it to zero.

**It does not tolerate a failed item.** A join with one failed child fails,
for the reason a failed dependency blocks its dependents: letting the
synthesis start on eleven of twelve answers builds on work the runtime already
judged wrong, and the failure would surface later under the synthesis's name.
A tolerance would be a new decision, taken separately.

**It does not touch the graph.** Children are the runtime's; `is_dag_complete`,
the node gate and the graph state never see them. The node still finishes when
its declared tasks do, and the gate still decides whether the phase advances.
A run never spans nodes, so an expansion's source is a task in the same node —
never a phase the session walked before the run started.

## Open questions

- Should `criticality: critical` imply `verifiers: 3`? The routing policy
  already ties the model to criticality, and tying the panel's size to it
  would be the same argument. Left as an explicit field until a real run shows
  the default is wanted.
- Should a failed item block the join or be reported and skipped? Blocked, for
  the reason above. A `tolerate` field is the smallest change if real runs
  show the strict rule is wrong more often than it is right.
