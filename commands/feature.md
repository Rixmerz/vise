---
description: Start the phase-gated feature workflow — design → implement → test → validate → commit
argument-hint: <what to build>
effort: high
---

Drive this feature through the vise `feature-dev` workflow.

1. Call `graph_activate` with `graph_name: "feature-dev"`.
2. Record the goal with `goal_set` (or `goal_bootstrap`) from the task below.
3. Enter the **design** phase first — do NOT write code yet. Follow each phase's
   injected prompt. When you reach **implement**, delegate to the matching
   specialist per the orchestration skill's fleet table (backend-<lang>,
   frontend, db-migrator); do trivial glue yourself.
4. Let the gates and `vise:reviewer` do their job before committing.

Feature: $ARGUMENTS
