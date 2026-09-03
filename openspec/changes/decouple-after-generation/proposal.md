# Decouple after generation, not during

## Why

`codelayer_gate` opens with the diagnosis:

> A well-factored repo costs an agent *more* to read than a badly-factored one
> — more files, more jumps, more context burned — so structure and
> navigability pull against each other and the agent resolves the tension by
> writing coupled code.

The symbol layer removed the *read* cost, and that was the half it could reach.
There is a second cost it does not touch, and it is the one that actually
decides what gets written: producing decoupled code means serialising a graph.
Generation is one linear stream, and coupled code *is* that stream. Decoupled
code is a non-linear structure flattened into a stream — boundaries decided
before the solution is understood, names chosen for things not yet understood,
coherence kept across pieces generated separately. That cost survives the
symbol layer intact, and so does the incentive to couple.

The `codelayer` skill already argues, without meaning to, for the fix:

> The first two [call sites] are also the two you understand least, so the
> abstraction you extract from them is the one most likely to be wrong.

That is an argument for deciding late. A transform applied *after* the code
works is deciding late — and it is the only point at which the decision can be
checked, because only then is there an oracle: the tests that already pass.

## What Changes

- A `decouple` phase, entered after `tests_pass` is green and exited by the
  same gate. The agent writes the way generation is cheapest — linear, in one
  place — and the phase turns that into the structure a reader wants, with the
  suite as the arbiter of whether behaviour survived.
- The phase composes existing pieces: `search_similar` and `analyze_impact`
  from livespec for the *where*, a builder task for the *how*, `tests_pass`
  plus `diff_scope` for the *whether*. It authors no new gate — a phase that
  wrote its own pass condition would be grading its own homework.
- The `codelayer` skill's "When NOT to decouple" section becomes the phase's
  refusal list, verbatim. The rule of three, the ~100-line floor, migrations
  and tests out of scope: the phase declines those rather than deciding them.
- A `decouple` node in `feature-dev-graph.yaml`, between `test` and
  `validate`, off by default until the phase has been run against a real repo
  three times (the same bar `codelayer` set for `enforce`).

## What Does Not Change

- No new validator. The phase exits through `tests_pass` and `diff_scope`.
- Nothing runs where livespec is not mounted; the phase reports that it could
  not look and passes through. A phase that guesses at structure without an
  index is the under-engineering it exists to prevent, with a worse name.
- The generator is not asked to write differently. That is the point.

## Open Questions

- **Where it lives.** The symbol index is livespec's. The transform can be a
  vise phase that calls livespec, or a livespec capability vise gates on.
  The first keeps vise's "recomposes, never authors a gate" rule legible; the
  second keeps the index and the thing that reads it in one place.
- **Who pays.** The non-linearity cost does not disappear; it changes payer.
  A second model call does the serialising, with full information and an
  oracle the first never had. That is a real gain and not a free one, and the
  phase should report what it spent next to what it changed.
