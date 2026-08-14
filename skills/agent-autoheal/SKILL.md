---
name: agent-autoheal
description: Two-path protocol for failing subagents. Hot path (during task) — re-brief the same agent with the quoted failure, append the incident to that agent's charter-keyed ledger in experience memory, escalate after 2 misses; never edits agent files. Cold path (batched heal) — only when that ledger shows the SAME failure slug twice for one agent, classify root cause and apply ONE surgical fix. Use after a subagent fails review/validators, or when experience_query on an agent's charter path surfaces a repeat failure shape.
---

# agent-autoheal

Incidents are keyed on **the agent's own charter file** — `agents/<name>.md` (or
`.claude/agents/<name>.md`). That is the file the cold path may end up editing, so
`experience_query` on that path is the retrieval. Read **Storage limits** before trusting a count.

## Hot path — during task (cheap, NEVER edits agents)

- Dispatch → review/validators fail → **RE-BRIEF**: re-dispatch the SAME agent, quoting the concrete failure verbatim in the prompt.
- Pass → done. Append the incident to that agent's ledger — read, then re-record:
  1. `experience_query(file_path="agents/<name>.md", scope="project")`
  2. `experience_record(type="gate_blocked", file_path="agents/<name>.md", severity="high", description="<top match verbatim> ;; agent:<name> <shape-slug> — <verbatim failure>")`
  Carry the top match's description **verbatim**, even when it holds another agent's segments — a record shorter than the incumbent is discarded while still reporting success. Filter by `agent:<name>` when reading and counting, never before writing. First incident = the segment alone, no separator.
- Fail 2nd time → **escalate**: different agent, or the orchestrator does it. Never a 3rd identical attempt.

`gate_blocked` because review and validators ARE gates; the landed fix is `gate_resolved`, a separate entry that never inflates the failure count.

## Cold path — the actual heal (batched, evidence-driven)

**Trigger:** the `gate_blocked` ledger for `agents/<name>.md` contains the same
`agent:<name> <shape-slug>` **twice**. One failure = anecdote; two = pattern.

Only then:

1. Read the N incidents — they are the `;;` segments of that one description.
2. Classify the common root cause (pick one):
   - **(a) Briefing gap** — fix the orchestrator's dispatch pattern, NOT the agent.
   - **(b) Charter gap** — add ONE surgical DO/DON'T rule (<10 lines) to that charter file, with the concrete example from the incidents.
   - **(c) Tool gap** — add the tool to frontmatter `tools:`.
   - **(d) Procedure gap** — write/extend a runbook (`.claude/runbooks/<agent>/<case>.md`), not the charter.
   - **(e) Model/effort mismatch** — adjust model or effort setting.
3. Apply the one fix.
4. `experience_record(type="gate_resolved", file_path="agents/<name>.md", description="agent:<name> <shape-slug> healed", resolution="<the one edit>")`

## Verification

- The next REAL dispatch is the test — no synthetic re-runs.
- Agent .md files are read fresh per spawn — no session restart needed.

## Where the heal is allowed to land

Check this before applying a **(b) charter gap** fix, because the obvious target
is the wrong one half the time.

- `.claude/agents/<name>.md` — the project's own agent. **Editable.** It is
  under version control and survives everything.
- `<plugin-root>/agents/<name>.md` — a bundled vise agent, living wherever the
  plugin was installed. **A charter edit here is silently reverted on the next
  plugin update.** Never write the heal there.

To heal a bundled agent, copy the charter to `.claude/agents/<name>.md` first
and edit the copy — a project-level agent of the same name takes precedence over
the plugin's. Say in your report that you shadowed a bundled agent, because the
copy now stops receiving upstream improvements to that charter.

If the failure is really vise's fault rather than this project's, the fix is a
bug report against vise, not a local shadow. Prefer classification **(a)
briefing gap** or **(d) procedure gap** for bundled agents — both land in files
the project owns and neither gets overwritten.

## Guards

- Never delete existing rules while adding one.
- Agent files <150 lines; over → split into runbooks.
- One incident never justifies a charter edit.
- Never edit a charter inside the plugin install directory — shadow it under
  `.claude/agents/` instead.

## Storage limits — a convention, not a feature

- A naming convention on a **file-keyed** store. No experience tool takes a tag
  parameter and none can query by agent; retrieval matches file paths only.
- The store merges records sharing (type, generalized path, domain) and keeps only the
  **longest** description — hence append-only. Never rewrite a count in place: `x1` → `x2`
  is the same length, so the store silently keeps `x1` and the bump is lost.
- `occurrences` counts records on the entry, not repeats of one shape — count the slug.
- One entry can hold two agents' segments (`agents/debugger.md` and `agents/frontend.md`
  both generalize to `agents/*.md`, domain `general`). The `agent:<name>` prefix separates
  them — filter by it, and leave `min_score` at default; raising it hides such a ledger.
