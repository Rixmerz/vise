---
name: lua-rules
description: Lua conventions — locals by default, explicit nil handling, 1-based indexing, careful metatables. Use ONLY when the file under edit or review is Lua (.lua); do NOT apply to any other language.
---

# Lua Rules

> Apply ONLY when the file under edit or review is Lua (`.lua`). If the current
> file is not Lua, do not use this skill — it does not apply to other languages.

## DO
- Declare every variable `local` — never rely on globals implicitly
- Return a module table at the end of a file; keep helpers local
- Remember tables/arrays are 1-based; use `#t` only on sequences without holes
- Handle fallible calls with `pcall`/`xpcall`; return `nil, err` for recoverable errors
- Check for `nil` explicitly — only `nil` and `false` are falsy (`0` and `""` are truthy)
- Prefer `ipairs` for arrays, `pairs` for maps; don't mutate a table while iterating it
- Localize hot globals (`local insert = table.insert`) in performance-critical loops
- Set string patterns carefully — Lua patterns are not regex

## DON'T
- Don't create accidental globals by forgetting `local`
- Don't assume `#t` is defined when the table has `nil` gaps
- Don't overuse metatables/`__index` magic where a plain function is clearer
- Don't use `==` across different types expecting coercion — Lua does not coerce in `==`
- Don't rely on table iteration order from `pairs`
- Don't `error()` with a bare string across module boundaries without context
- Don't leave `print` debugging in committed code — use the host's logger
