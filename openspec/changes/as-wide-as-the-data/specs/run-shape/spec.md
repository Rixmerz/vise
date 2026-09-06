## ADDED Requirements

### Requirement: A task may expand into one task per item of an upstream result

The runtime SHALL, for a task declaring `for_each`, read a list from the named
source task's artifact when that source succeeds, create one child task per
item as an ordinary task, and complete the declaring task as a join once every
child has succeeded.

#### Scenario: One child per item

- **WHEN** the source's artifact carries three items under the declared key
- **THEN** three children are dispatched, each briefed with its own item, and no worker is dispatched for the declaring task

#### Scenario: A child is a task like any other

- **WHEN** a child's worker fails with a wrong answer
- **THEN** it escalates one rung, carrying its previous attempt, exactly as an undeclared task would

#### Scenario: The join succeeds when its children do

- **WHEN** every child has succeeded
- **THEN** the declaring task is `SUCCEEDED` at no cost, and a `collection` artifact under its id lists the items and each child's outcome

#### Scenario: One failed child fails the join

- **WHEN** one of twelve children ends `FAILED`
- **THEN** the declaring task is blocked, its reason names that child, and tasks depending on it do not start

#### Scenario: The list is empty

- **WHEN** the source's artifact carries the key with an empty list
- **THEN** the declaring task succeeds with zero children and its note says so

#### Scenario: The list is missing

- **WHEN** no artifact of the source carries the key
- **THEN** the declaring task is `BLOCKED`, and its reason names the source, the key, and the keys the source did carry

#### Scenario: The cap is reported, not silent

- **WHEN** the list is longer than `max_items`
- **THEN** exactly `max_items` children are created and an event records how many items were dropped

#### Scenario: Downstream sees the children

- **WHEN** a task depends on the declaring task
- **THEN** its brief carries the children's artifacts and the collection

#### Scenario: A resumed run re-expands to the same children

- **WHEN** a run that expanded is resumed
- **THEN** the same child ids are derived, succeeded children are kept, and the rest are re-attempted

#### Scenario: A replan keeps the children

- **WHEN** a replan happens after an expansion
- **THEN** the children are still in the plan and are not cancelled as dropped

### Requirement: The plan states what it cannot know

`vise runtime plan` SHALL show an expanding task once, with its source and its
cap, and price it as a range from one child to the cap.

#### Scenario: The width is unknown

- **WHEN** a plan contains a `for_each` task
- **THEN** the render names the source and the cap, and says the width is decided at run time

#### Scenario: The cost is a range

- **WHEN** a plan contains a `for_each` task
- **THEN** the estimated cost is the floor and the ceiling is rendered beside it

#### Scenario: A ceiling that does not fit is a note

- **WHEN** the ceiling exceeds the remaining budget and the floor does not
- **THEN** the plan carries a note and `problems` stays empty

### Requirement: A task may run until it stops finding

The runtime SHALL, for a task declaring `until`, re-dispatch it after each
passing attempt until the declared number of consecutive rounds adds nothing
new under the declared key, or the declared maximum of rounds is reached.

#### Scenario: Two quiet rounds stop it

- **WHEN** `stable_for: 2` and the third and fourth rounds add nothing the first two had not found
- **THEN** the task succeeds after the fourth round with the union of what was found

#### Scenario: The maximum bounds it

- **WHEN** `max_rounds: 3` and every round finds something new
- **THEN** the task succeeds after the third round and the record says the maximum was reached

#### Scenario: Each round sees what was found

- **WHEN** a second round is dispatched
- **THEN** its brief lists what earlier rounds found, marked as not to be reported again

#### Scenario: A failed round is a failure

- **WHEN** a round's worker fails
- **THEN** it takes the ladder as any failed attempt, and does not count as a quiet round

#### Scenario: The dedup is code

- **WHEN** a round reports an item already seen
- **THEN** it is counted as nothing new without asking any agent
