---
name: rust-rules
description: Rust coding conventions — error handling, ownership idioms, unsafe discipline, cargo tooling. Use ONLY when the file under edit or review is Rust (.rs); do NOT apply to any other language.
---

# Rust Rules

> Apply ONLY when the file under edit or review is Rust (`.rs`). If the current
> file is not Rust, do not use this skill — it does not apply to other languages.

Precedence: `engineering-baseline` settles conflicts — safety outranks
everything, and the project's existing conventions outrank every preference
stated below.

## DO
- Use `?` for error propagation
- Give libraries a concrete error enum and applications a boxed/erased error; `thiserror` and `anyhow` are the greenfield defaults for those two shapes
- Use `expect("reason")` over `unwrap()` when panic is intentional
- Add `// SAFETY:` comment to every `unsafe` block
- Use iterators (`.iter().map().filter().collect()`) over manual index loops
- Use enums for state modeling with exhaustive `match`
- Use the Newtype pattern for type safety (`struct UserId(u64)`)
- Use Typestate pattern when invalid states should not compile
- Derive `Debug`, `Clone`, `PartialEq` where applicable
- Use `&str` in function params, return `String` when owned
- Use `Cow<str>` when data frequently passes through unmodified
- Put logic in `lib.rs`, keep `main.rs` as thin wrapper
- Use `[workspace.dependencies]` for shared dependency versions
- Run `cargo fmt`, `cargo clippy -D warnings`, `cargo audit` in CI
- Document public APIs with `# Examples`, `# Errors`, `# Panics`
- Use `lto = true` and `codegen-units = 1` in release profile
- Before changing a `pub` signature, run `findReferences` on it; for a trait method run `goToImplementation` to get every implementor. Each caller and implementor in that list compiles against the new signature or is updated in this change

## DON'T
- Don't use `unwrap()` in production — lacks context
- Don't use `.clone()` reflexively to silence borrow checker — restructure code
- Don't write `unsafe` without documented justification
- Don't use multiple lifetimes on structs when owning data (`String` vs `&str`) works
- Don't ignore compiler warnings or clippy lints
- Don't use `String` params when `&str` suffices (forces caller allocation)
- Don't write C-style index loops — iterators are idiomatic and faster
- Don't use `async-std` — obsolete (unmaintained since early 2024), use Tokio or smol
- Don't use `Box<dyn FnMut>` callbacks for observer — use channels
- Don't add `#[inline]` without profiling data
- Don't ignore `cargo audit` results in CI

## Security — outranks every rule above

See `engineering-baseline` for the general floor. These are the language-specific footguns:

- Use bound parameters (`sqlx::query!`, `.bind(..)`) — never `format!` into SQL
- `Command::new(..).arg(..)` with separate arguments, never `sh -c` with interpolation
- Use `OsRng`/`getrandom` for tokens and a constant-time compare (`subtle`) for secrets
- Every `unsafe` block is a security surface — an undocumented one is a finding, not a style note
- Prefer checked arithmetic (`checked_add`, `try_into`) wherever the value derives from input
