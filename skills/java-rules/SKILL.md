---
name: java-rules
description: Java coding conventions — immutability, Optional discipline, resource safety, modern language features. Use ONLY when the file under edit or review is Java (.java); do NOT apply to any other language.
---

# Java Rules

> Apply ONLY when the file under edit or review is Java (`.java`). If the current
> file is not Java, do not use this skill — it does not apply to other languages.

## DO
- Prefer `final` fields and immutable objects; use `record` for data carriers
- Use `Optional<T>` for return values that may be absent — never for fields or params
- `try-with-resources` for anything `AutoCloseable`; never manual `close()` in `finally`
- Program to interfaces (`List`, `Map`), instantiate concretes (`ArrayList`, `HashMap`)
- Use `java.time` (`Instant`, `LocalDate`) — never `Date`/`Calendar`
- Use `var` for local inference only when the type is obvious from the right side
- Validate arguments early: `Objects.requireNonNull(x, "x")`
- Prefer `enum` over int/string constants
- Use `equals`/`hashCode` together, or a `record`; keep them consistent
- Streams for transformation pipelines; plain loops when clearer or hot-path
- Catch the most specific exception; wrap with context via a cause

## DON'T
- Don't return `null` collections — return `List.of()` / `Collections.emptyList()`
- Don't catch `Exception`/`Throwable` broadly or swallow with an empty block
- Don't use raw types (`List` instead of `List<String>`)
- Don't do field injection when constructor injection is possible
- Don't use `synchronized` on a public object you don't own; prefer `java.util.concurrent`
- Don't concatenate strings in loops — use `StringBuilder`
- Don't call overridable methods from a constructor
- Don't use checked exceptions for control flow
- Don't leave `System.out.println` in production code — use a logger (SLF4J)
