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


def _resolve_ref(ref: str, inputs: dict[str, Any], step_outputs: dict[str, Any]) -> Any:
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
        return inputs[key]

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
        var_name = parts[1]
        value = os.environ.get(var_name)
        if value is None:
            raise KeyError(f"template ref '{ref}': env var '{var_name}' is not set")
        return value

    raise KeyError(f"template ref '{ref}': unknown namespace '{parts[0]}' (expected inputs, steps, env)")


def render_value(value: Any, inputs: dict[str, Any], step_outputs: dict[str, Any]) -> Any:
    """Recursively render template expressions in *value*.

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
            return _resolve_ref(lone.group(1).strip(), inputs, step_outputs)

        def replace(m: re.Match) -> str:  # type: ignore[type-arg]
            return str(_resolve_ref(m.group(1).strip(), inputs, step_outputs))
        return _TEMPLATE_RE.sub(replace, value)

    if isinstance(value, dict):
        return {k: render_value(v, inputs, step_outputs) for k, v in value.items()}

    if isinstance(value, list):
        return [render_value(item, inputs, step_outputs) for item in value]

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
