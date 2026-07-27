"""A recipe-backed node gate must not pass on a plan it never executed.

``run_recipe`` returns a PLAN — vise cannot dispatch another MCP server's
tools. A fully-bound recipe therefore returns ``success: True`` having run
nothing. The gate used to read that field directly, which would have let a
declared recipe gate report green without a single step having happened.

This is the same false-green shape the capability bindings were fixed for:
a gate that reports on work it did not do is worse than no gate.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from vise.engines.node_gate import _run_node_validators


def _bind(project_dir: Path, assignments: dict[str, str]) -> None:
    d = project_dir / ".vise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "capabilities.yaml").write_text(yaml.dump(assignments), encoding="utf-8")


def _write_recipe(project_dir: Path, name: str, capability: str) -> None:
    d = project_dir / ".vise" / "recipes"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(
        yaml.dump({
            "name": name,
            "description": "one bound step",
            "steps": [{"id": "a", "capability": capability, "args": {"x": "1"}}],
        }),
        encoding="utf-8",
    )


async def test_bound_recipe_gate_does_not_pass_on_an_unexecuted_plan(tmp_path: Path):
    _bind(tmp_path, {"firecrawl.scrape_url": "x.gate.step"})
    _write_recipe(tmp_path, "gate-recipe", "x.gate.step")
    node = SimpleNamespace(id="n1", validators=None, recipe="gate-recipe")

    gate = await _run_node_validators(node, str(tmp_path))

    assert gate is not None, "node declares a recipe, so the gate must run"
    assert gate["passed"] is False, "gate passed on a plan that was never executed"
    failed = [f for f in gate["failed"] if f["name"].startswith("recipe:")]
    assert failed, "the recipe gate produced no failure entry"
    evidence = failed[0]["evidence"]
    assert "not executed" in evidence
    # The caller must be told what to run, not just that it failed.
    assert "firecrawl" in evidence and "scrape_url" in evidence


async def test_missing_recipe_still_fails_the_gate(tmp_path: Path):
    node = SimpleNamespace(id="n1", validators=None, recipe="does-not-exist")
    gate = await _run_node_validators(node, str(tmp_path))
    assert gate is not None
    assert gate["passed"] is False
    failed = next(f for f in gate["failed"] if f["name"].startswith("recipe:"))
    assert "not found" in failed["evidence"]
