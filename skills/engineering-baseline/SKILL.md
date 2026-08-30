---
name: engineering-baseline
description: The language-agnostic rules every vise agent carries, plus the precedence rule that settles conflicts between the project's own conventions, a `*-rules` skill, and `ponytail`. Use on every code-touching task, in any language — it is what the per-language rules skills sit on top of. Also use when two loaded instructions contradict each other and you need to know which one wins.
---

# engineering-baseline

Two things live here: **the precedence rule** (which instruction wins when
loaded instructions disagree) and **the general rules** that hold in every
language. The `*-rules` skills sit on top of this file; they never replace it.

## Precedence — highest wins, no exceptions

An agent runs with several sources of instruction loaded at once, and they do
conflict. `python-rules` says "use uv"; the repo has a `requirements.txt` and a
pinned pip. `ponytail` says "stdlib first"; `typescript-rules` says "validate
with Zod". Without a stated order, the agent picks whichever it read last.

1. **The user's explicit request.** If the user asked for it, it happens —
   even when a rule below calls it a bad idea. Say the concern once, then build
   what was asked.
2. **Safety and correctness.** Injection, authz, secrets, data loss, memory
   safety. Never traded away for style, brevity, or convention-matching.
3. **The project's existing conventions.** What the repo already does wins over
   what any skill prefers. A repo on pip and Flake8 stays on pip and Flake8; a
   repo throwing exceptions does not get one `Result<T, E>` island. Convention
   drift costs more than the better tool buys.
4. **The `*-rules` skill for the file's language.** Applies where the project
   has no established position.
5. **`ponytail` minimalism.** The tie-breaker between two options that both
   satisfy 1–4: take the shorter one.

### The tooling corollary

A `*-rules` skill naming a third-party package (`Pydantic`, `Zod`, `structlog`,
`thiserror`, `chi`) is a **recommendation for greenfield code**, and greenfield
means the project has no incumbent. It is never a license to add a dependency
to a repo that already solved that problem another way. Rung 3 outranks rung 4,
and `ponytail`'s ladder — stdlib, then platform, then a dependency already in
the manifest, then a new one — governs how you satisfy the rule.

Adding a dependency is a decision, not a detail: state which rung of the ladder
failed and why, in your report.

## General rules — every language

### Errors
- Fail loudly at the boundary, not silently three layers in. A swallowed error
  is a bug that will be reported by a user instead of a test.
- Never catch broadly and continue. Catch the narrowest type you can handle;
  everything else propagates.
- Attach context when you wrap: what was being attempted, with which input.
  `"failed"` is not a diagnosis.
- An empty `catch`/`except`/`rescue` needs a comment saying why the failure is
  genuinely safe to ignore.

### Security — non-negotiable, outranks every style rule
- Parameterize every query. String interpolation into SQL, shell, HTML, LDAP,
  or a template is the finding, regardless of how trusted the input looks
  (CWE-89, CWE-78, CWE-79).
- Validate external input at the trust boundary: user input, API responses,
  environment variables, file contents, message payloads (CWE-20).
- Never commit a secret — not in code, not in a config default, not in a test
  fixture, not in a comment. Read them from the environment or a secret store
  (CWE-798).
- Never log credentials, tokens, full card numbers, or personal data
  (CWE-532, CWE-359).
- Deny by default on authz. An endpoint with no explicit check is unprotected,
  not "protected by default" (CWE-862).
- Use a CSPRNG for anything security-bearing, and a constant-time compare for
  anything secret (CWE-330, CWE-208).

Cite the CWE when you report a finding — `security-baseline` has the surface
index, the severity ladder, and the triage protocol for a dependency CVE. The
language's own footguns live in that language's `*-rules` skill.

### Dependencies
- Follow `ponytail`'s ladder before adding one.
- A new dependency needs a stated reason why the stdlib and the existing
  manifest could not do it.
- Pin versions the way the project already pins them. Never add an unpinned
  dependency to a repo that pins (CWE-1104).
- Update the lockfile in the same commit as the manifest.
- A new dependency is a new trust relationship: confirm it is the package you
  meant, not a typosquat, and never fetch build inputs without an integrity
  check (CWE-494).
- Never dismiss an SCA advisory from memory. Quote the tool's output, establish
  whether the vulnerable path is reachable, and prefer the version bump —
  `security-baseline` has the protocol.

### Naming and structure
- Name for the reader at the call site, not for the implementation.
- No generic buckets: `utils`, `helpers`, `common`, `misc`, `manager`, `data`.
  If the name does not say what is inside, the module has no single job.
- Match the file's existing structure and idiom before introducing a new one.
- Delete dead code rather than commenting it out. Version control remembers.

### Comments
- Comment *why*, never *what*. The code says what.
- Do not narrate the change in a comment (`// added null check`) — that belongs
  in the commit message.
- Keep comments true. A stale comment is worse than no comment.

### Tests
- A test that has never been observed failing has not been shown to test
  anything.
- Test the entrypoint the system actually calls, not a helper it may bypass.
- No sleeps for async — poll or await a condition.
- Each test owns its data; no order dependence, no shared mutable state.

### Concurrency
- Every blocking operation in async code goes to a thread/executor, never on
  the event loop.
- Every spawned task is owned by something that cancels it. No orphans.
- Respect cancellation — a task that ignores its cancellation signal leaks.

### Finding things — the language server before grep

`grep` matches text. A language server resolves bindings. When the thing you
are looking for is a *symbol* — a function, type, method, field — reach for
`LSP` first; when it is a string, a config key, a log message or a TODO, grep
is correct and the server has nothing to say.

The four that pay for themselves, each against the search you would otherwise
run:

| Question | `LSP` | why not grep |
|---|---|---|
| who calls this? | `findReferences` | grep finds the name; it misses aliased imports and re-exports, and it hits comments, strings and unrelated same-named symbols |
| where does this come from? | `goToDefinition` | the import path is often a barrel or a re-export, not the definition |
| what implements this? | `goToImplementation` | implementations rarely name the thing they implement |
| what does this file contain? | `documentSymbol` | reading 800 lines to find three methods |

`hover` answers "what type is this actually" for anything inferred or generic,
which is a question the source text cannot answer at all.

Two honest limits. **Dynamic dispatch is invisible to every language server** —
reflection, `send`, `getattr`, string-keyed DSLs — so in those languages grep
the name as well. And **no server, no answer**: if the language has none
installed, `LSP` returns nothing, and that is a reason to fall back to text
search and say so, never to report a caller list as complete.

### Changing a signature
- Before changing a public signature, resolve the caller set with `LSP`
  (`findReferences`, plus `goToImplementation` for an interface member).
- Dynamic dispatch hides call sites from every language server — grep the name
  too where the language allows reflection, `send`, `getattr`, or DSL callbacks.
- Every caller and implementor either satisfies the new signature or is updated
  in the same change.
- No language server for that language? Fall back to text search and say in
  your report that the caller list is unverified.

### Reporting done
- "Done" is a claim, and a claim needs the command and its result.
- Report: files touched, the verify command + its actual output, anything
  deliberately left undone and why.
- Never report green on a suite you did not run.
