---
name: cpp-rules
description: C and C++ conventions — RAII, ownership, no raw new/delete, bounds and lifetime safety. Use ONLY when the file under edit or review is C/C++ (.c/.h/.cpp/.cc/.hpp); do NOT apply to any other language.
---

# C / C++ Rules

> Apply ONLY when the file under edit or review is C or C++
> (`.c`/`.h`/`.cpp`/`.cc`/`.cxx`/`.hpp`). If the current file is neither, do not
> use this skill — it does not apply to other languages.

## DO (C++)
- RAII for every resource; wrap ownership in `unique_ptr`/`shared_ptr`, never raw owning pointers
- Prefer stack objects and value semantics; pass big objects by `const&`
- Use `std::` containers/algorithms over hand-rolled loops and C arrays
- Mark single-arg constructors `explicit`; mark overrides `override`; mark leaf classes `final`
- Follow the Rule of Zero (or Five if you manage a resource by hand)
- Use `enum class`, `constexpr`, `nullptr`, and `[[nodiscard]]` where they add safety
- Use `std::span`/`std::string_view` for non-owning views (C++20/17)
- Check every allocation / fallible call; propagate errors explicitly

## DO (C)
- Check the return of `malloc`/`realloc`/`fopen` etc.; free exactly once
- Pair every allocation with a single owner responsible for freeing it
- Use `sizeof(*ptr)` in allocations; bound every buffer write (`snprintf`, not `sprintf`)

## DON'T
- Don't use `new`/`delete`/`malloc`/`free` directly in modern C++ — use smart pointers/containers
- Don't return references/pointers to locals; don't use after free/move
- Don't use `strcpy`/`strcat`/`gets`/`sprintf` — use bounded variants
- Don't rely on implementation-defined or undefined behavior (signed overflow, aliasing)
- Don't cast away `const`; don't C-cast in C++ — use `static_cast`/`reinterpret_cast`
- Don't ignore compiler warnings — build with `-Wall -Wextra` and a sanitizer in CI
