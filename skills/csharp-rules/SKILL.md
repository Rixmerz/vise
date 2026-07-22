---
name: csharp-rules
description: C# conventions — nullable reference types, async/await discipline, IDisposable, LINQ hygiene. Use ONLY when the file under edit or review is C# (.cs); do NOT apply to any other language.
---

# C# Rules

> Apply ONLY when the file under edit or review is C# (`.cs`). If the current
> file is not C#, do not use this skill — it does not apply to other languages.

## DO
- Enable nullable reference types (`#nullable enable`) and honor the annotations
- `async`/`await` all the way; return `Task`/`Task<T>`; suffix async methods with `Async`
- Pass and observe `CancellationToken` on async APIs
- Use `ConfigureAwait(false)` in library code
- `using` declarations for `IDisposable`; implement `IAsyncDisposable` for async cleanup
- Prefer `record`/`readonly struct` for immutable data
- Use expression-bodied members and pattern matching where they clarify
- Use `IEnumerable<T>`/`IReadOnlyList<T>` at boundaries; materialize with `ToList()` once
- Throw specific exceptions; use `ArgumentNullException.ThrowIfNull(x)`

## DON'T
- Don't use `async void` except for event handlers
- Don't block on async (`.Result`, `.Wait()`, `.GetAwaiter().GetResult()`) — deadlock risk
- Don't ignore the nullable warnings with `!` unless provably safe
- Don't expose mutable collections as public fields — use properties/read-only views
- Don't catch `Exception` broadly or swallow it silently
- Don't enumerate an `IEnumerable` multiple times when it may be a deferred/expensive query
- Don't use `string` concatenation in loops — use `StringBuilder`/`string.Join`
- Don't leave `Console.WriteLine` for diagnostics — use `ILogger`
