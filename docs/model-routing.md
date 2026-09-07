# Model routing

Every task in a run gets a model and a reasoning effort. Picking them per task
instead of per session is most of the cost difference between an orchestrator
that is worth running and one that is not — and picking them *badly* is worse
than not picking at all, because a task silently downgraded to a model that
cannot do it fails in a way that looks like a hard problem.

This document states the inputs, the defaults, the ladder, and — the part most
routing tables skip — the evidence behind the defaults.

## Inputs

The router reads six things and nothing else:

| Input | Where it comes from | Effect |
|---|---|---|
| `role` | the task | selects the policy row, and the candidate agents |
| `complexity` | the planner's estimate, `trivial…high` | only `high` raises the floor; `medium` is the unstated default and must be a no-op |
| `criticality` | the task, `routine…critical` | `critical` pins to the top tier |
| `attempts` | the run's own history | each prior failure climbs one rung |
| `context_size` | resolved context, in tokens | large context raises effort, never lowers it |
| `budget_remaining` | the run ledger | can veto, never promote |

Deliberately absent: how long the last task took, how the model "felt", and any
notion of confidence the model reports about itself. A model's stated confidence
is not evidence about the model's output, and routing on it makes the router a
consumer of exactly the signal it is supposed to check.

`budget_remaining` can only veto. A router that promotes on a full budget spends
the budget because it is there.

## The policy

The table is the policy. The ladder below is a different thing — the path
escalation walks when a policy default fails — and conflating them is a mistake
worth naming, because the first implementation of this module made it: each role
mapped to a ladder *index*, which made any default that is not a rung
(documentation at `haiku/medium`) inexpressible, and silently rewrote it to the
nearest rung. A policy that cannot state its own defaults is not a policy.

| Work | Role | Model | Effort |
|---|---|---|---|
| extraction, classification | `extract`, `classify` | haiku | — |
| file inventory | `inventory` | haiku | — |
| research | `research` | sonnet | medium |
| documentation | `docs` | haiku | — |
| ordinary coding | `backend`, `frontend` | sonnet | medium |
| testing | `test` | sonnet | medium |
| debugging | `debug` | sonnet | high |
| integration | `integration` | sonnet | high |
| architecture | `architecture` | opus | high |
| security-critical change | `security` | opus | high |
| adversarial review | `review` | opus | high |
| replanning | `replan` | opus | high |

Defaults, not law. Every cell is overridable per task and per repo.

Six rows have no bundled charter: `extract`, `classify`, `inventory`,
`integration`, `architecture` and `replan`. A task declaring one gets a price and
then a registry miss, which `resolve` reports rather than papering over by picking
whichever agent sorts first. `PROJECT_SUPPLIED_ROLES` in `test_project_agents.py`
names them with a reason each, and two tests there hold the list closed in both
directions — a newly priced role must ship an agent or join the list, and a role
that gains one must leave it.

## Effort is not a dial every model has

The [effort parameter][effort] names the models it supports and Claude Haiku 4.5
is not among them. So `haiku/low` and `haiku/medium` — the cheapest rung and the
documentation row, as first written — named a setting the model has no dial for.
Nothing failed loudly: the adapter put `--effort medium` on the command line, and
the run reported an effort that reached nothing.

`supports_effort()` in `runtime/contracts.py` is the one answer to that question,
and the router, the brief's own rendering and the adapter all ask it there rather
than each keeping a list. Where it says no, the effort is empty, a decision
renders as the bare model name, and `--effort` never reaches the command line. A
task that *pins* an effort onto such a model keeps the model — a pin is absolute —
and loses the effort, with the reason recorded in the decision.

It sits in `contracts.py`, the layer that imports nothing of its own, for the
usual reason: two copies of a list like this drift, and the half that drifts is
the one nobody runs.

[effort]: https://platform.claude.com/docs/en/build-with-claude/effort

## The charter and the policy state the same thing

A bundled agent declares its own `model` and `effort` in frontmatter, and that is
what Claude Code reads when a session delegates to it. The table above is what
`vise runtime` reads. Both mean "the default for this kind of work", and for
eight roles both exist — so for eight roles vise held two answers.

Six agreed. Two did not: `docs-writer` declared `sonnet`/`low` against
documentation's `haiku`, and `backend-cpp` and `backend-rust` declared `high`
against ordinary coding's `medium`. The same agent behaved differently depending
on which door the work arrived through, and nothing anywhere said so.

`test_agent_charters_match_the_routing_policy` now holds them equal: where a role
has both a policy row and a bundled charter, the charter states that row. That
leaves the precedence rule below intact and, for bundled roles, unexercised — it
still decides what happens to a *project-local* charter, which is where a
disagreement can still arise and where the policy should still win.

Per-language variation inside one role stays inexpressible, deliberately:
`backend` is one role for twelve charters. A C++ task that needs more than
`sonnet/medium` says so as `complexity: high`, which raises the floor for that
task. Difficulty is a property of the work, not of the file extension — and a
charter that encodes it applies it to every task in that language forever,
including the one-line ones.

### Precedence

Highest first:

1. **A model the task pins** is absolute, escalation included. A person wrote
   that line; the router has no standing to overrule it.
2. **The policy for the kind of work**, adjusted by criticality, complexity and
   the attempt history below.
3. **The agent charter's own model** — a *default*, not a floor, and used only
   for a role the policy does not cover.

Point 3 was wrong in the first implementation and is worth stating twice. A
charter that declares `sonnet` is saying what that agent runs at when nobody
else has an opinion; reading it as a floor let `docs-writer`'s `sonnet` overrule
documentation's `haiku`, which made the entire cheapest tier unreachable through
any bundled agent — and the symptom looked like a property of the charters
rather than a bug in the router.

One rule overrides the table outright: `criticality == "critical"` routes to the
top tier regardless of the row. `criticality == "elevated"` adds one rung, and
`complexity == "high"` raises the floor to `sonnet/high`.

## The ladder

```
haiku ──fail──► sonnet/medium ──fail──► sonnet/high ──fail──► opus/high
```

One rung per failed attempt, and only for failures where the work was attempted
and was wrong. A timeout or a missing binary is a retry at the same rung; a
wrong answer is an escalation. Escalating on infrastructure failures spends the
top tier on a problem no model would have solved.

The ladder terminates. Nothing escalates past `opus/high`; a task that fails
there goes to replanning, which is a different question — *was this the right
task* — asked by a different agent.

## Evidence for the defaults

The two most expensive knobs were measured rather than guessed. Both sweeps ran
headless in isolated throwaway repos, concurrently, checked against test oracles
written independently of the pipeline — not against what the pipeline claimed
about itself. The numbers are from [mini-vise](https://github.com/Rixmerz/mini-vise),
whose three-node pipeline is small enough to sweep exhaustively.

**Orchestrator effort — one trivial task, three efforts:**

| effort | cost | wall time | orchestrator output tokens | ran its own self-check |
|---|---|---|---|---|
| high | $1.05 | 145 s | 3399 | yes |
| medium | $0.92 | 116 s | 2161 | yes |
| low | $0.73 | 77 s | 171 | **no** |

All three delegated correctly and shipped identical code. `medium` is the
default: 12% cheaper than `high` for the same output, and unlike `low` it still
ran the self-check before calling the task done. `low` is real money, but the one
behaviour it dropped was a safety step — the class of corner-cutting that is
invisible on an easy task and shows up only when it matters.

**Implementer model — sonnet vs opus, three harder tasks, two reps each, effort
pinned at medium:** 11 of 12 runs shipped correct, verified code regardless of
model. Cost scaled roughly 2× — $0.82 per run on sonnet, $2.09 on opus — and the
single timeout was an opus run.

The one real difference is worth stating precisely, because it argues against the
obvious reading. Feeding every implementation `'٥'` (Arabic-Indic digit five,
mentioned in no spec) showed both sonnet-authored duration parsers silently
accepting it. The opus-authored parser rejected it correctly — but only because
its own first draft had introduced a related bug that its own review lap caught.
Opus did not make fewer mistakes; it made a mistake shaped so the same reviewer
happened to catch it. Two samples per cell is a pattern, not a proof.

**So implementers stay on sonnet.** The gap that sweep found is a *reviewer
charter* problem — tell the reviewer to probe parsers and validators with
non-ASCII input — and fixing it there costs nothing per run, where paying 2× on
every implementation task costs on every run forever.

The general lesson, which the table above encodes: spend the expensive model
where the work is **noticing**, not where it is **applying**. Adding a guard and
writing asserts is mechanical. Seeing that a green test pins a bug is not.

## Explainability

Every routing decision is recorded with the inputs that produced it, and
`vise runtime explain <run>` renders them:

```
task 17  backend-auth   sonnet/high

  escalated from sonnet/medium
    · attempt 2 failed, classified code_bug
    · criticality: elevated (touches auth)
  additional budget: ~$0.18
```

This is not a nicety. A router whose decisions cannot be read back is a router
nobody can correct, and the first time it spends $4 on a two-line change the only
available response is to switch it off.

## Related

- [`scheduler.md`](scheduler.md) — where retry, escalate and replan differ
- [`worker-contract.md`](worker-contract.md) — what a worker owes back
