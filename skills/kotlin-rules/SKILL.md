---
name: kotlin-rules
description: Kotlin conventions — null safety, immutability, coroutines discipline, idiomatic scope functions. Use ONLY when the file under edit or review is Kotlin (.kt/.kts); do NOT apply to any other language.
---

# Kotlin Rules

> Apply ONLY when the file under edit or review is Kotlin (`.kt`/`.kts`). If the
> current file is not Kotlin, do not use this skill — it does not apply to other
> languages.

## DO
- Prefer `val` over `var`; prefer immutable collections (`listOf`, `mapOf`)
- Use nullable types deliberately; handle with `?.`, `?:`, `let` — not `!!`
- Model closed sets with `sealed class`/`enum`; use `when` exhaustively (no `else`)
- Use `data class` for value objects; `object` for singletons
- Structured concurrency: launch coroutines in a scope, respect cancellation, use `suspend`
- Use `Dispatchers.IO`/`Default` appropriately; never block a coroutine thread
- Use extension functions to keep call sites readable, sparingly
- Use scope functions with intent: `let` (nullable), `apply` (config), `run`/`with` (compute)
- Return `Result<T>` or throw specific exceptions — be consistent per module

## DON'T
- Don't use `!!` to silence nullability — restructure or check
- Don't use `lateinit` where a nullable or constructor value fits
- Don't run blocking I/O inside a coroutine without a proper dispatcher
- Don't leak `GlobalScope` coroutines — tie them to a lifecycle scope
- Don't overuse companion objects as dumping grounds for statics
- Don't write Java-style getters/setters — use properties
- Don't ignore platform types from Java interop — annotate/assert nullability at the boundary
