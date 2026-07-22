---
name: ruby-rules
description: Ruby coding conventions — idiomatic blocks, safe navigation, frozen strings, small methods. Use ONLY when the file under edit or review is Ruby (.rb/.rake/.gemspec); do NOT apply to any other language.
---

# Ruby Rules

> Apply ONLY when the file under edit or review is Ruby (`.rb`/`.rake`/`.gemspec`).
> If the current file is not Ruby, do not use this skill — it does not apply to
> other languages.

## DO
- Add `# frozen_string_literal: true` at the top of every file
- Prefer guard clauses (`return unless x`) over nested conditionals
- Use `&.` (safe navigation) instead of manual nil checks
- Use keyword arguments for methods with 2+ params or any boolean flag
- Prefer `each`/`map`/`select`/`reduce` over manual index loops
- Use `attr_reader`/`attr_accessor` instead of hand-written accessors
- Raise specific `StandardError` subclasses, not bare `RuntimeError`/strings
- Use `fetch` for required hash keys so missing keys fail loudly
- Keep methods short; extract private helpers freely
- Follow standard naming: `snake_case` methods/vars, `CamelCase` classes, `?`/`!` suffixes

## DON'T
- Don't rescue `Exception` — rescue `StandardError` or a specific subclass
- Don't use `and`/`or` for boolean logic (precedence traps) — use `&&`/`||`
- Don't mutate a method argument the caller still owns
- Don't monkey-patch core classes in library/app code
- Don't use `for` loops — use iterators
- Don't leave `puts`/`p` debugging in committed code — use a logger
- Don't overuse metaprogramming when a plain method is clearer
- Don't ignore `rubocop` offenses without an inline disable + reason
