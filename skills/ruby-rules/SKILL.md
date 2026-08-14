---
name: ruby-rules
description: Ruby coding conventions — idiomatic blocks, safe navigation, frozen strings, small methods. Use ONLY when the file under edit or review is Ruby (.rb/.rake/.gemspec); do NOT apply to any other language.
---

# Ruby Rules

> Apply ONLY when the file under edit or review is Ruby (`.rb`/`.rake`/`.gemspec`).
> If the current file is not Ruby, do not use this skill — it does not apply to
> other languages.

Precedence: `engineering-baseline` settles conflicts — safety outranks
everything, and the project's existing conventions outrank every preference
stated below.

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
- Before changing a public method signature, run `findReferences` on it, then grep the name too — `send`, `method_missing`, and DSL callbacks are call sites no server resolves. Every caller found satisfies the new signature or is updated in this change

## DON'T
- Don't rescue `Exception` — rescue `StandardError` or a specific subclass
- Don't use `and`/`or` for boolean logic (precedence traps) — use `&&`/`||`
- Don't mutate a method argument the caller still owns
- Don't monkey-patch core classes in library/app code
- Don't use `for` loops — use iterators
- Don't leave `puts`/`p` debugging in committed code — use a logger
- Don't overuse metaprogramming when a plain method is clearer
- Don't ignore `rubocop` offenses without an inline disable + reason

## Security — outranks every rule above

See `engineering-baseline` for the general floor. These are the language-specific footguns:

- Parameterize (`where("x = ?", v)`, `where(x: v)`) — never interpolate into a query string, `order`, or `pluck`
- Never `system`/backticks/`eval` with interpolated input — pass an argument array to `system`/`Open3`
- `YAML.safe_load` and never `Marshal.load` on untrusted data
- `SecureRandom` for tokens, `Rack::Utils.secure_compare` for comparison, bcrypt for passwords
- Never `send`/`constantize`/`public_send` with a user-supplied name
