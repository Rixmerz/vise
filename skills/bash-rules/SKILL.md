---
name: bash-rules
description: Shell scripting conventions — strict mode, quoting, safe expansion, no eval, portable exits. Use ONLY when the file under edit or review is a shell script (.sh/.bash/.zsh, or a file with a shell shebang), or when writing a non-trivial one-off shell pipeline; do NOT apply to any other language.
---

# Shell Rules

> Apply ONLY when writing or reviewing shell — a `.sh`/`.bash`/`.zsh` file, a
> file with a shell shebang, or a non-trivial command you are about to run.
> If the current file is not shell, do not use this skill.

Precedence: `engineering-baseline` settles conflicts. Match the project's
existing scripts before introducing a new style.

## DO
- Start every script with `#!/usr/bin/env bash` and `set -euo pipefail`
- Quote every expansion: `"$var"`, `"$@"`, `"${arr[@]}"` — unquoted is a bug
  waiting for a space or a glob in the value
- Use `"${var:?message}"` for required inputs so a missing value fails loudly
- Use `$(...)` for command substitution, never backticks
- Use `[[ ... ]]` in bash for tests; `(( ... ))` for arithmetic
- Prefer `mktemp -d` for scratch space and remove it in a `trap ... EXIT`
- Use `--` before user-controlled arguments to `rm`, `grep`, `cp`, `mv`
- Check that a command exists before using it: `command -v foo >/dev/null`
- Use `local` for every variable inside a function
- Give the script a `usage()` and exit non-zero on bad arguments
- Prefer arrays over space-joined strings for argument lists
- Use `find ... -print0 | xargs -0` (or `-exec ... +`) for filenames

## DON'T
- Don't use `eval` — there is almost always a function or an array instead
- Don't parse `ls` output; use a glob or `find`
- Don't `cd` without checking it succeeded, and prefer absolute paths over
  relying on the current directory
- Don't `rm -rf` a path built from an unvalidated variable — an empty variable
  turns `rm -rf "$dir/"` into `rm -rf /`
- Don't pipe a downloaded script straight into a shell
- Don't hide failures: a `|| true` needs a comment saying why it is safe
- Don't ignore that `set -e` does not fire inside a condition, a `&&` chain, or
  a subshell in a pipeline — check exit codes explicitly there
- Don't write shell past ~100 lines of logic; that is the point where Python
  becomes the smaller solution
- Don't leave `shellcheck` findings unaddressed without an inline
  `# shellcheck disable=SCxxxx` and a reason

## Security — outranks every rule above

See `engineering-baseline` for the general floor. These are the surface-specific footguns:

- Never `eval`, and never pass a user-controlled string to `sh -c` — build an argument array
- Quote every expansion; an unquoted `$var` containing a space or a glob is a command-injection vector
- Never `rm -rf` a path built from an unvalidated variable — set `${dir:?}` so an empty value aborts instead of expanding to `/`
- Never pipe a download straight into a shell, and verify checksums on anything you do fetch
- Keep secrets out of the command line — arguments are visible in `ps`; pass them via the environment or a file descriptor
- Set restrictive permissions (`umask 077`) before writing anything that holds a credential
