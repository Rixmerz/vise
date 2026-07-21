---
name: agent-autoheal
description: Diagnose a failing subagent and rewrite its .claude/agents/<name>.md definition to prevent recurrence. Use after a subagent returns wrong/incomplete work, fails review, or repeats the same mistake twice.
---

# agent-autoheal

Operational procedure for healing a failing subagent definition.

## 1. Capture evidence

- Collect: failing subagent's name, the exact prompt it received, its full report, and the review verdict / diff showing the failure.
- Quote the actual failure verbatim. Never diagnose from memory or a paraphrase.

## 2. Classify the root cause (pick exactly one)

- **(a) Briefing gap** — the prompt lacked context. Fix the orchestrator's dispatch pattern, NOT the agent .md.
- **(b) Charter gap** — the agent .md is missing a rule/constraint/tool guidance.
- **(c) Capability gap** — the agent lacks a tool it needs. Add it to frontmatter `tools:`.
- **(d) Knowledge gap** — a project convention is missing. Add to the agent .md or a runbook.
- **(e) Model/effort mismatch** — task too hard/easy for the agent's model or effort setting.

## 3. Rewrite

- Edit `.claude/agents/<name>.md`: add the specific rule derived from the failure, phrased as DO/DON'T with the concrete example from step 1.
- One failure = one targeted addition. Never rewrite the whole charter.
- Keep the addition under ~10 lines.

## 4. Record

- Call `experience_record` with the failure→fix pair so the lesson persists cross-project.
- If the same failure shape recurs, promote the fix to a runbook (`.claude/runbooks/<agent>/<case>.md`).

## 5. Verify

- Re-dispatch the SAME task to the improved agent.
- The fix is proven only when the re-run passes review.
- Agent .md files are read fresh at each spawn — no session restart needed; changes take effect on next dispatch.

## 6. Guards

- Max 2 heal iterations per agent per task; then escalate to human.
- Never delete existing rules while adding a new one.
- Keep agent files under ~150 lines; if over, split content into runbooks.
