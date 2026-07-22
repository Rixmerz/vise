---
name: php-rules
description: PHP coding conventions — strict types, typed properties, PSR standards, safe SQL. Use ONLY when the file under edit or review is PHP (.php); do NOT apply to any other language.
---

# PHP Rules

> Apply ONLY when the file under edit or review is PHP (`.php`). If the current
> file is not PHP, do not use this skill — it does not apply to other languages.

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

## DON'T
- Don't suppress errors with `@`
- Don't use `extract()`, `eval()`, or variable variables (`$$x`)
- Don't build SQL/HTML/shell strings from user input without escaping
- Don't rely on loose truthiness for `0`/`""`/`"0"` — check explicitly
- Don't use globals or `static` mutable state for request data
- Don't mix business logic into templates
- Don't return mixed `false`-or-value sentinels — throw or return null with a nullable type
- Don't ignore `phpstan`/`psalm` findings without justification
