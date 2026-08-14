---
name: java-rules
description: Java coding conventions — immutability, Optional discipline, resource safety, modern language features. Use ONLY when the file under edit or review is Java (.java); do NOT apply to any other language.
---

# Java Rules

> Apply ONLY when the file under edit or review is Java (`.java`). If the current
> file is not Java, do not use this skill — it does not apply to other languages.

Precedence: `engineering-baseline` settles conflicts — safety outranks
everything, and the project's existing conventions outrank every preference
stated below.

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
- Before changing a `public`/`protected` signature, run `findReferences` on it; for an interface or abstract method run `goToImplementation`. Overloads and implementing classes are what a method-name grep gets wrong. Every entry in that list compiles against the new signature or is updated in this change

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

## Security — outranks every rule above

See `engineering-baseline` for the general floor. These are the language-specific footguns:

- `PreparedStatement` with placeholders — never string-concatenated SQL, and never a concatenated JPQL/HQL query
- Never deserialize untrusted data with `ObjectInputStream`
- `ProcessBuilder` with an argument list, never a joined shell string
- `SecureRandom` for tokens, `MessageDigest.isEqual` for comparison, `password_hash`-grade KDFs (bcrypt/scrypt/Argon2) for passwords
- Disable external entities on every XML parser and document builder (XXE)
