"""Template renderer for recipe step args.

Supports three substitution namespaces:
  {{ inputs.X }}           — from recipe invocation inputs
  {{ steps.ID.output.K }}  — from a previous step's output dict
  {{ env.VAR }}            — from environment variables (rendered at call time)

Raises KeyError with a descriptive message for unknown references.
Env values are NEVER stored in telemetry — only the var name reference is kept.
"""
from __future__ import annotations

import os
import re
from typing import Any

# Matches {{ ... }} with optional whitespace
_TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
# Matches a string that is EXACTLY one {{ ref }} with no surrounding text.
_LONE_REF_RE = re.compile(r"^\{\{\s*([^}]+?)\s*\}\}$")


def _resolve_ref(
    ref: str, inputs: dict[str, Any], step_outputs: dict[str, Any],
    *, env_literal: bool = False,
) -> Any:
    """Resolve a single template reference like 'inputs.X' or 'steps.A.output.B'.

    Returns the NATIVE resolved value (dict/list/scalar) — callers that embed
    the ref in surrounding text stringify it themselves.
    """
    parts = ref.split(".", 2)

    if parts[0] == "inputs":
        if len(parts) < 2:
            raise KeyError(f"template ref '{ref}': expected inputs.<name>")
        key = parts[1]
        if key not in inputs:
            raise KeyError(f"template ref '{ref}': input '{key}' not provided")
        # Walk the rest of the path. `split(".", 2)` computed it and then threw
        # it away, so `{{ inputs.cfg.url }}` returned the whole of `cfg` — no
        # error, the wrong value, and where `cfg` also held a token, a leak.
        current = inputs[key]
        for segment in (parts[2].split(".") if len(parts) > 2 and parts[2] else []):
            if isinstance(current, dict):
                if segment not in current:
                    raise KeyError(
                        f"template ref '{ref}': no key '{segment}' at that path"
                    )
                current = current[segment]
                continue
            if isinstance(current, list) and segment.isdigit():
                index = int(segment)
                if index >= len(current):
                    raise KeyError(
                        f"template ref '{ref}': index {segment} is past the end"
                    )
                current = current[index]
                continue
            raise KeyError(
                f"template ref '{ref}': cannot look up '{segment}' in a "
                f"{type(current).__name__}"
            )
        return current

    if parts[0] == "steps":
        if len(parts) < 3:
            raise KeyError(f"template ref '{ref}': expected steps.<id>.output.<key>")
        step_id = parts[1]
        rest = parts[2]  # e.g. "output.path"
        rest_parts = rest.split(".", 1)
        if rest_parts[0] != "output":
            raise KeyError(f"template ref '{ref}': only steps.<id>.output.<key> is supported")
        out_key = rest_parts[1] if len(rest_parts) > 1 else ""
        if step_id not in step_outputs:
            raise KeyError(f"template ref '{ref}': step '{step_id}' has not run yet")
        output = step_outputs[step_id]
        if not isinstance(output, dict):
            raise KeyError(f"template ref '{ref}': step '{step_id}' output is not a dict")
        if out_key not in output:
            raise KeyError(f"template ref '{ref}': step '{step_id}' output has no key '{out_key}'")
        return output[out_key]

    if parts[0] == "env":
        if len(parts) < 2:
            raise KeyError(f"template ref '{ref}': expected env.<VAR>")
        if len(parts) > 2 and parts[2]:
            # An environment variable name cannot contain a dot, so this is a
            # mistake — and silently reading `parts[1]` would hand back a value
            # the author did not ask for.
            raise KeyError(
                f"template ref '{ref}': env var names contain no dots; "
                f"expected env.<VAR>"
            )
        var_name = parts[1]
        if env_literal:
            # The caller wants a copy safe to persist. Hand back the reference,
            # never the value — see `render_value`.
            return f"{{{{ {ref} }}}}"
        value = os.environ.get(var_name)
        if value is None:
            raise KeyError(f"template ref '{ref}': env var '{var_name}' is not set")
        return value

    raise KeyError(f"template ref '{ref}': unknown namespace '{parts[0]}' (expected inputs, steps, env)")


def render_value(
    value: Any, inputs: dict[str, Any], step_outputs: dict[str, Any],
    *, env_literal: bool = False,
) -> Any:
    """Recursively render template expressions in *value*.

    ``env_literal=True`` resolves ``inputs`` and ``steps`` normally and returns
    the ``{{ env.VAR }}`` token itself for the env namespace. That is how the
    telemetry copy is built.

    It has to be *built* that way rather than un-built afterwards. The old path
    rendered first and then called `redact_env_refs` on the result — which only
    rewrites strings that still contain a literal `{{ env.X }}` token, and after
    rendering none do. So the redaction was a no-op and the secret went to
    `runs.jsonl` in plaintext, under three docstrings promising the opposite.

    Strings:
      - A string that is EXACTLY one ``{{ ref }}`` (no surrounding text)
        resolves to the NATIVE value (dict/list/scalar) so structured args
        like a layout contract pass through unmangled.
      - A ref embedded in surrounding text is stringified in place.
    Dicts/lists: recurse into values.
    Other scalars: returned as-is.
    """
    if isinstance(value, str):
        lone = _LONE_REF_RE.match(value)
        if lone is not None:
            return _resolve_ref(lone.group(1).strip(), inputs, step_outputs,
                                env_literal=env_literal)

        def replace(m: re.Match) -> str:  # type: ignore[type-arg]
            return str(_resolve_ref(m.group(1).strip(), inputs, step_outputs,
                                    env_literal=env_literal))
        return _TEMPLATE_RE.sub(replace, value)

    if isinstance(value, dict):
        return {k: render_value(v, inputs, step_outputs, env_literal=env_literal)
                for k, v in value.items()}

    if isinstance(value, list):
        return [render_value(item, inputs, step_outputs, env_literal=env_literal)
                for item in value]

    return value


def redact_env_refs(args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *args* with env var values replaced by their reference string.

    Used for telemetry to avoid storing secret values.
    Any string value that *contains* a {{ env.VAR }} reference is replaced with
    the literal reference string (e.g. "{{ env.SECRET }}") rather than the
    resolved value.
    """
    def _redact(value: Any) -> Any:
        if isinstance(value, str):
            def replace_with_ref(m: re.Match) -> str:  # type: ignore[type-arg]
                ref = m.group(1).strip()
                parts = ref.split(".", 1)
                if parts[0] == "env":
                    return f"{{{{ {ref} }}}}"
                return m.group(0)  # not an env ref, leave as-is
            return _TEMPLATE_RE.sub(replace_with_ref, value)
        if isinstance(value, dict):
            return {k: _redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value

    return {k: _redact(v) for k, v in args.items()}
