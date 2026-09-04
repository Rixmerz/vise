## 1. Lineage

- [x] 1.1 `RunSpec.parent_run_id`, serialised and read back
- [x] 1.2 `status` names the run a continuation came from

## 2. Carrying the ledger

- [x] 2.1 `Scheduler.continue_from(prior, spec, tasks)` beside `resume`
- [x] 2.2 The new ledger opens at the prior spend, per task and in total
- [x] 2.3 A `continued` event, registered in `_VALID_RUN_EVENTS`

## 3. The command

- [x] 3.1 `vise runtime continue <run_id> --graph <path> [--node]`
- [x] 3.2 `--skip-done` subtracts the prior run's successes; the default runs
      what the plan declares and says which ids that affects
- [x] 3.3 The plan prints with the inherited spend; `--yes` to dispatch
- [x] 3.4 Exit 3 when the new plan has nothing left to do

## 4. Tests

- [x] 4.1 The ceiling bounds the chain: spend carries, remaining budget shrinks
- [x] 4.2 ~~A dependency on a prior success is satisfied without redeclaring
      it~~ — impossible and rightly so; `Graph.validate` refuses it. Recorded
      in the design instead of dropped
- [x] 4.3 A redeclared task runs anyway, and `--skip-done` reaches the plan
- [x] 4.4 `parent_run_id` survives save and load
- [x] 4.5 Nothing is dispatched and no state is written without `--yes`
- [x] 4.6 Re-broken: each fix turns exactly the test that names it red

## 5. Verification

- [x] 5.1 ruff clean
- [x] 5.2 Full suite green after `coverage combine`; floor holds
- [x] 5.3 Exercised against a real recorded run, not only in tests
- [x] 5.4 CHANGELOG

- [x] 5.5 A test for the ledger carrying, which the re-break pass found missing
