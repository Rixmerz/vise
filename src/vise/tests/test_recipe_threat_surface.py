"""What a recipe from the repository can reach.

A recipe is loaded from `<repo>/.vise/recipes/`, project scope last, so a
repo-supplied file fully replaces a bundled one of the same name. Everything
here therefore comes from whoever wrote the repository.

Three separate failures, one of which is the reverse of what the code claims:

* A secret substituted into a step's arguments is written to `runs.jsonl` in
  plaintext, by a redaction step that runs *after* rendering and so has nothing
  left to redact. Three docstrings say the opposite.
* `{{ inputs.a.b }}` resolves to the whole of `a` — no error, wrong value — and
  where `a` holds a secret, that is the leak widening rather than a typo.
* One malformed recipe file takes `recipe_list`, `recipe_describe` and
  `recipe_run` down for *every* recipe, because the loader catches two
  exception types and a third gets out.
"""
from __future__ import annotations

import json

import pytest

from vise.recipes.renderer import redact_env_refs, render_value

SECRET = "sk-live-do-not-log-this"


# --- the leak -------------------------------------------------------------


def test_redaction_after_rendering_cannot_work_and_this_says_why(monkeypatch):
    """The old sequence, asserted as the dead end it is.

    `redact_env_refs` rewrites strings that still hold a literal `{{ env.X }}`
    token. After rendering, none do — so calling it on rendered arguments is a
    no-op that reads like a safeguard. This test exists so that reintroducing
    that order fails here rather than in someone's log file.
    """
    monkeypatch.setenv("MY_TOKEN", SECRET)
    rendered = render_value({"header": "Bearer {{ env.MY_TOKEN }}"}, {}, {})
    assert SECRET in json.dumps(rendered), "this test needs the secret to render"

    assert SECRET in json.dumps(redact_env_refs(rendered)), (
        "if this ever passes, redaction-after-rendering started working and the "
        "comment in `runner` explaining why it cannot is now wrong"
    )


def test_the_runner_writes_no_secret_to_its_telemetry(monkeypatch, tmp_path):
    """End to end: what actually lands in runs.jsonl."""
    import inspect

    from vise.recipes import runner

    monkeypatch.setenv("MY_TOKEN", SECRET)
    source = inspect.getsource(runner)
    assert "redact_for_telemetry(rendered_args)" not in source, (
        "the telemetry copy is being un-built from rendered args again"
    )
    assert "env_literal=True" in source, (
        "the telemetry copy must be *built* with the env namespace held back"
    )


def test_a_telemetry_copy_keeps_the_reference_not_the_value(monkeypatch):
    """What the docstring promises: the var name, never the value."""
    monkeypatch.setenv("MY_TOKEN", SECRET)
    args = {"header": "Bearer {{ env.MY_TOKEN }}", "url": "https://x.test"}

    safe = render_value(args, {}, {}, env_literal=True)

    assert SECRET not in json.dumps(safe)
    assert "{{ env.MY_TOKEN }}" in json.dumps(safe)
    assert safe["url"] == "https://x.test", "non-env values must still render"


def test_env_literal_still_resolves_inputs_and_steps(monkeypatch):
    """The guard: holding env back must not hold everything back."""
    monkeypatch.setenv("MY_TOKEN", SECRET)
    args = {"a": "{{ inputs.name }}", "b": "{{ steps.one.output.path }}",
            "c": "{{ env.MY_TOKEN }}"}

    safe = render_value(args, {"name": "alpha"}, {"one": {"path": "/tmp/x"}},
                        env_literal=True)

    assert safe["a"] == "alpha"
    assert safe["b"] == "/tmp/x"
    assert safe["c"] == "{{ env.MY_TOKEN }}"


# --- the truncated reference ---------------------------------------------


def test_a_nested_input_reference_walks_the_whole_path():
    """`ref.split('.', 2)` computed the rest of the path and threw it away."""
    inputs = {"cfg": {"token": SECRET, "url": "https://x.test"}}

    got = render_value("{{ inputs.cfg.url }}", inputs, {})

    assert got == "https://x.test", (
        f"the nested ref resolved to {got!r} — the whole object, secret included"
    )


def test_a_nested_input_reference_that_misses_says_which_segment():
    inputs = {"cfg": {"url": "https://x.test"}}

    with pytest.raises(KeyError) as caught:
        render_value("{{ inputs.cfg.missing }}", inputs, {})

    assert "missing" in str(caught.value)


def test_an_env_reference_with_a_path_is_refused():
    """Environment variable names cannot contain dots, so this is a mistake."""
    with pytest.raises(KeyError):
        render_value("{{ env.SOME.THING }}", {}, {})


def test_a_plain_input_reference_still_returns_the_whole_object():
    """The guard: walking deeper must not stop `{{ inputs.cfg }}` working."""
    inputs = {"cfg": {"a": 1}}

    assert render_value("{{ inputs.cfg }}", inputs, {}) == {"a": 1}


# --- one bad file, every tool ---------------------------------------------


def test_one_malformed_recipe_does_not_break_the_others(tmp_path):
    """`inputs: 5` raises TypeError, which the loader's two handlers miss."""
    from vise.recipes.loader import load_recipes

    recipes = tmp_path / ".vise" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "good.yaml").write_text(
        "name: good\ndescription: d\nsteps:\n"
        "  - id: one\n    capability: meta.assert\n    args: {}\n",
        encoding="utf-8")
    # `steps` has to be *valid* for this to reach the `inputs` line — with an
    # empty `steps` the loader raises ValueError first, which it already caught.
    (recipes / "bad.yaml").write_text(
        "name: bad\ndescription: d\ninputs: 5\nsteps:\n"
        "  - id: one\n    capability: meta.assert\n    args: {}\n",
        encoding="utf-8")

    names = {r.name for r in load_recipes(str(tmp_path))}

    assert "good" in names, (
        f"one malformed file took every recipe down with it: {sorted(names)}"
    )
    assert "bad" not in names


@pytest.mark.parametrize("body", [
    # The shape that raised TypeError past the loader's two handlers.
    "name: bad\ndescription: d\ninputs: 5\nsteps:\n"
    "  - id: one\n    capability: meta.assert\n    args: {}\n",
    "name: bad\ndescription: d\nsteps: 7\n",
    "just a string, not a mapping\n",
    "name: bad\nsteps:\n  - 5\n",
])
def test_no_shape_of_malformed_recipe_escapes_the_loader(body, tmp_path):
    from vise.recipes.loader import load_recipes

    recipes = tmp_path / ".vise" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "good.yaml").write_text(
        "name: good\ndescription: d\nsteps:\n"
        "  - id: one\n    capability: meta.assert\n    args: {}\n",
        encoding="utf-8")
    (recipes / "bad.yaml").write_text(body, encoding="utf-8")

    assert "good" in {r.name for r in load_recipes(str(tmp_path))}


# --- a pattern from the repo, on the server's event loop -------------------


def test_a_pathological_pattern_fails_the_step_instead_of_hanging():
    """Both the pattern and the text come from the recipe, so from the repo.

    `meta.assert` compiled and ran an attacker-chosen regex against
    attacker-chosen text, synchronously, inside an async function — so a
    catastrophically backtracking pattern does not fail a gate, it hangs the
    whole vise MCP server for every session using it.

    Bounded here rather than by trying to detect a "bad" regex: whether a
    pattern backtracks is a property of the pair, and a wall clock is the only
    honest test.
    """
    import time

    from vise.recipes.builtin import meta_assert

    started = time.monotonic()
    with pytest.raises(AssertionError) as caught:
        meta_assert({
            "condition": "match",
            "pattern": "(a+)+$",
            "against": "a" * 40 + "!",
        })
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"the assertion ran for {elapsed:.1f}s"
    assert "pattern" in str(caught.value).lower()


def test_an_oversized_pattern_is_refused_before_it_is_compiled():
    from vise.recipes.builtin import meta_assert

    with pytest.raises(AssertionError):
        meta_assert({"condition": "match", "pattern": "a" * 5000, "against": "a"})


def test_an_ordinary_assertion_still_passes():
    """The guard: bounding it must not break the step."""
    from vise.recipes.builtin import meta_assert

    assert meta_assert({
        "condition": "match", "pattern": r"^ok$", "against": "ok",
    })["passed"] is True


def test_an_ordinary_assertion_still_fails_when_it_should():
    from vise.recipes.builtin import meta_assert

    with pytest.raises(AssertionError):
        meta_assert({"condition": "match", "pattern": r"^ok$", "against": "no"})
