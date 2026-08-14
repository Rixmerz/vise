---
name: php-rules
description: PHP coding conventions — strict types, typed properties, PSR standards, safe SQL. Use ONLY when the file under edit or review is PHP (.php); do NOT apply to any other language.
---

# PHP Rules

> Apply ONLY when the file under edit or review is PHP (`.php`). If the current
> file is not PHP, do not use this skill — it does not apply to other languages.

Precedence: `engineering-baseline` settles conflicts — safety outranks
everything, and the project's existing conventions outrank every preference
stated below.

## DO
- Start every file with `declare(strict_types=1);`
- Type every property, parameter, and return (including `void`/`never`/nullable `?T`)
- Use `===`/`!==` (strict comparison) — never `==`
- Use constructor property promotion and `readonly` for immutable data
- Use `enum` (PHP 8.1+) instead of class constants for closed sets
- Use prepared statements / parameter binding for all SQL — never interpolate input
- Follow PSR-12 formatting and PSR-4 autoloading
- Throw typed exceptions; catch specific types, add context
- Prefer `match` over `switch` for value mapping (strict, exhaustive)
- Use `??` and `?->` instead of `isset()` ladders
- Before changing a public function or method signature, run `findReferences` on it; for an interface method run `goToImplementation` to get the implementing classes. Every caller and implementor in that list satisfies the new signature or is updated in this change

## DON'T
- Don't suppress errors with `@`
- Don't use `extract()`, `eval()`, or variable variables (`$$x`)
- Don't build SQL/HTML/shell strings from user input without escaping
- Don't rely on loose truthiness for `0`/`""`/`"0"` — check explicitly
- Don't use globals or `static` mutable state for request data
- Don't mix business logic into templates
- Don't return mixed `false`-or-value sentinels — throw or return null with a nullable type
- Don't ignore `phpstan`/`psalm` findings without justification

## Security — outranks every rule above

See `engineering-baseline` for the general floor. These are the language-specific footguns:

- Prepared statements with bound parameters for every query — this is the single most common finding in PHP code
- `password_hash`/`password_verify` for passwords — never md5, sha1, or a hand-rolled salt
- `random_bytes`/`random_int` for tokens, `hash_equals` for comparison
- Escape output for its destination context — `htmlspecialchars` for HTML, never raw echo of user data
- Never `unserialize()` untrusted input; use `json_decode`
