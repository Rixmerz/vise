## 1. Comments

- [x] 1.1 `parse_value` resolves a quoted scalar by its closing quote
- [x] 1.2 `parse_value` resolves an inline sequence by its closing bracket
- [x] 1.3 The enforcer's `_scalar` learns the same rule so the two stay in agreement
- [x] 1.4 `#fff`, `url#anchor` and a `#` inside quotes are untouched

## 2. Telemetry

- [x] 2.1 Pin emitted run events against `_VALID_RUN_EVENTS`, both directions
- [x] 2.2 Guard the scan itself, so a broken regex cannot report success

## 3. Tests

- [x] 3.1 Block-list item: quoted, unquoted, single-quoted, with and without a comment
- [x] 3.2 The two parsers agree on a commented item, end to end through a real graph
- [x] 3.3 The bundled library still agrees — zero divergence across all ten
- [x] 3.4 Re-broken: the old quote ordering fails the agreement test; a naive `_scalar` fails four; removing `resumed` from the registry fails two

## 4. Verification

- [x] 4.1 ruff clean
- [x] 4.2 Full suite green with `coverage combine`; floor holds
- [x] 4.3 CHANGELOG, and the stale "Known" entry replaced by the fix
