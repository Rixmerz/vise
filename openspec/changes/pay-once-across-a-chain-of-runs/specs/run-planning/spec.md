## ADDED Requirements

### Requirement: A run may continue a recorded run under a new plan

The runtime SHALL start a run of a different graph as the continuation of a
recorded run, carrying that run's spend and recording the link between them.

#### Scenario: The ceiling bounds the chain, not the link

- **WHEN** a run that spent $3.55 is continued under a plan with `--max-cost 4`
- **THEN** the plan reports that its tasks do not fit and the continuation refuses

#### Scenario: The spend is attributable, not a lump

- **WHEN** a continuation dispatches and finishes
- **THEN** its ledger holds the prior run's per-task spend alongside its own

#### Scenario: A task the composer put back in the plan

- **WHEN** the new graph declares a task whose id succeeded in the prior run
- **THEN** that task is dispatched, and the output names it as one that will run again

#### Scenario: The caller knows the redeclared task is the same work

- **WHEN** `--skip-done` is passed
- **THEN** the prior run's succeeded ids are subtracted from the plan

#### Scenario: The new plan gets its own records

- **WHEN** a continuation starts
- **THEN** it holds records only for the tasks its own graph declares

#### Scenario: The chain is readable afterwards

- **WHEN** a continuation is recorded
- **THEN** its spec names the run it continued, that name survives save and load,
  and the rendered status says which run it continues

### Requirement: Continuing does not dispatch without being told to

Continuing SHALL print the plan and the inherited spend and stop, unless the
caller asks for dispatch explicitly.

#### Scenario: No confirmation given

- **WHEN** `vise runtime continue` is run without `--yes`
- **THEN** nothing is dispatched, no run state is created, and the output states
  what would be spent on top of what already was

#### Scenario: The new plan has problems

- **WHEN** the composed graph plans with problems
- **THEN** the continuation refuses before dispatching anything

#### Scenario: The new plan has nothing left

- **WHEN** `--skip-done` leaves no task to run
- **THEN** the command exits 3, as `compose` and `resume` do for the same condition
