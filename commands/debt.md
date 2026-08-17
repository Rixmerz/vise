---
description: Freeze pre-existing duplication as accepted debt, or report what is frozen
effort: low
---

Manage the duplication debt baseline. Read `$ARGUMENTS`: `status` (default),
`capture`, or `reset`.

**status** — call `debt_baseline_status`. Report how many shapes are frozen and
when. If nothing is frozen, say plainly that `search_similar` is currently
reporting every match including pre-existing debt, and that this is why it
feels noisy in an existing repo.

**capture** — call `debt_baseline_capture`. Before doing it, confirm the repo is
in a state worth freezing: uncommitted work in progress will be frozen too, and
duplication introduced this week becomes accepted debt silently.

Report `scanned`, `shapes_frozen` and `frozen`. Note that a shape is frozen only
when it already had two or more copies — a unique function is not debt, so a
copy written tomorrow will still report. That is the distinction people get
wrong about this feature.

**reset** — call `debt_baseline_capture(reset=True)`. Say what it discards
first. Appropriate after a large merge; not appropriate as a way to silence a
finding you did not want to hear.
