---
name: codelayer
description: Read and change code through the symbol layer instead of by file path — read_unit, locate, search_similar, resolve_location. Load when working in a repo where VISE_CODELAYER is set, when a Read or Grep on source was denied, before writing a new helper, or when deciding whether a piece of code is worth decoupling. Covers when NOT to decouple, which is the part that keeps the layer from turning under-engineering into over-engineering.
---

# CodeLayer — reading and shaping code by symbol

The physical layout of a repo is a human affordance. You do not need the file
`payments.py`; you need `charge_card` — its body, the signatures of what it
calls, the definitions of the types in those signatures, what it raises, and
which tests cover it. That set is a **contract closure**, and asking for it
directly costs a fraction of what reading the file and its neighbours costs.

This matters beyond token economy. A well-factored repo is *more* expensive to
read by file than a badly-factored one: more files, more jumps, more context
burned. That is why an agent left to its own incentives writes coupled code —
decoupling makes its own next read harder. The symbol layer removes the
tension, which is what makes the rest of this skill's advice affordable.

## The tools, in the order you actually use them

| Question | Call |
|---|---|
| What exists around here? | `locate(query)` — candidates, no bodies |
| I need to change this one | `read_unit(qname)` — the contract closure |
| What breaks if I change it? | `analyze_impact` / `who_calls` |
| A stack trace points at `file:42` | `resolve_location(path, line)` |
| Does this helper already exist? | `search_similar(code)` — **before writing it** |

`locate` first, always. It is deliberately cheap and body-free: orientation
before commitment. Jumping straight to `read_unit` on a guessed name is how you
end up reading three closures to find the one you wanted.

## Reading the closure honestly

`read_unit` returns two lists that are not the same thing, and treating them as
one is the mistake to avoid:

- **`external_types`** — `str`, `Promise`, `DiGraph`, `Response`. Not in the
  index because a dependency owns them. Expected, not a gap.
- **`unresolved_types`** — named in a signature, neither defined in this
  project nor imported from outside it. **A real gap.** The closure promised
  it and did not deliver. Read it before relying on its shape; do not guess
  the fields.

`budget.degraded: true` means callees were dropped to fit. The farthest went
first, so what remains is the most relevant — but if the change turns on a
call that is not there, raise `token_budget` rather than guessing.

## Before writing any helper

Call `search_similar` with the body you are about to write. The duplicate you
are about to create has a *different name* — that is why it got written — so
grep cannot find it and neither can your memory of this codebase.

If it reports a match, import the existing one. If you believe the match is
wrong, say why in one line before proceeding; a silent override of this check
is how a codebase grows four date formatters.

## When NOT to decouple

This is the half that keeps the layer from trading under-engineering for
over-engineering. The tools make abstraction cheap, and cheap abstraction
applied by default is its own kind of damage — an indirection you have to read
through forever to save a duplication that never happened twice.

**The rule of three.** Do not extract a port, an interface, or a strategy until
the third consumer exists. Two call sites is a coincidence. The first two are
also the two you understand least, so the abstraction you extract from them is
the one most likely to be wrong.

**Size floor.** A module under roughly 100 lines stays one file. Splitting it
buys navigation you did not need and costs an import graph you now maintain.

**Out of scope entirely.** Scripts, migrations, generated code, and tests.
Migrations are append-only history; refactoring one rewrites the past.
Test duplication is often deliberate — a test that shares a helper with the
code it tests can pass for the wrong reason.

**Deleting an abstraction is a legitimate change.** If an indirection has
exactly one implementation and has had one for a year, collapsing it is
progress, not regression. Treat `collapse_indirection` as a peer of
`extract_port`, not a repair.

## The debt baseline

In an existing repo, run `debt_baseline_capture` once before trusting
`search_similar`'s silence. Without it, every legitimate pre-existing
near-duplicate reports, the noise buries the one finding that matters, and the
check gets switched off within the week.

What it freezes is a **violation** — two or more bodies that already shared a
shape — not a symbol. A lone `format_currency` is not debt, so a copy you write
tomorrow still reports. That distinction is the whole feature.

## When the gate denies a read

`VISE_CODELAYER=enforce` denies source reads by path. The denial names the
replacement call; make it. Do not route around with `cat`, `sed -n` or a
`Bash` heredoc — the gate catches the common ones, and defeating it costs you
the closure you would have got for free.

Config, tests, docs, migrations and manifests are never gated. If you were
denied on one of those, that is a bug worth reporting, not a workaround to
find.

`VISE_CODELAYER=off` disables it. Reach for that when the layer is wrong, not
when it is inconvenient — and say which, because "the gate misfired on X" is
the feedback that fixes it and "I turned it off" is not.

## Precedence

`engineering-baseline` outranks this skill; the project's existing conventions
outrank both. If a repo's structure disagrees with the advice here, the repo
wins — this skill describes how to work a codebase, not how a codebase must be
arranged.
