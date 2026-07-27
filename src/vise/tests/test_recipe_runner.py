"""Recipe runner produces a PLAN, not an execution — vise has no MCP
dispatch layer, so `run_recipe` resolves + renders each step and hands the
caller an ordered plan to execute itself.
"""
from pathlib import Path

import yaml

from vise.recipes.loader import Recipe, RecipeStep
from vise.recipes.runner import run_recipe


def _write_capabilities(project_dir: Path, assignments: dict[str, str]) -> None:
    cap_dir = project_dir / ".vise"
    cap_dir.mkdir(parents=True, exist_ok=True)
    (cap_dir / "capabilities.yaml").write_text(yaml.dump(assignments), encoding="utf-8")


async def test_bound_recipe_returns_plan_with_one_entry_per_step(tmp_path: Path) -> None:
    _write_capabilities(tmp_path, {"firecrawl.scrape_url": "x.two.step.first"})
    recipe = Recipe(
        name="two-step",
        description="two bound steps",
        inputs=[],
        steps=[
            RecipeStep(id="a", capability="x.two.step.first", args={"url": "https://x.test"}),
            RecipeStep(id="b", capability="x.two.step.first", args={"url": "https://y.test"}),
        ],
        source_path=tmp_path / "two-step.yaml",
    )
    result = await run_recipe(recipe, {}, tmp_path)

    assert result["success"] is True
    assert len(result["plan"]) == 2
    assert result["plan"][0] == {
        "step_id": "a",
        "capability": "x.two.step.first",
        "resolved_mcp": "firecrawl",
        "resolved_tool": "scrape_url",
        "args": {"url": "https://x.test"},
    }
    assert "execute" in result["message"]


async def test_step_output_ref_survives_verbatim_into_plan(tmp_path: Path) -> None:
    _write_capabilities(tmp_path, {"firecrawl.scrape_url": "x.chain.step"})
    recipe = Recipe(
        name="chained",
        description="second step references first step's output",
        inputs=[],
        steps=[
            RecipeStep(id="a", capability="x.chain.step", args={"url": "https://x.test"}),
            RecipeStep(id="b", capability="x.chain.step", args={"url": "{{ steps.a.output }}"}),
        ],
        source_path=tmp_path / "chained.yaml",
    )
    result = await run_recipe(recipe, {}, tmp_path)

    assert result["success"] is True
    assert result["plan"][1]["args"]["url"] == "{{ steps.a.output }}"


async def test_unbound_step_still_fails_loudly_with_no_plan(tmp_path: Path) -> None:
    recipe = Recipe(
        name="unbound-probe",
        description="single step on a deliberately unbound capability",
        inputs=[],
        steps=[RecipeStep(id="e2e", capability="validate.integration.e2e", args={})],
        source_path=tmp_path / "unbound-probe.yaml",
    )
    result = await run_recipe(recipe, {}, tmp_path)

    assert result["success"] is False
    assert "unresolved" in result["error"]
    assert "capability_set" in result["error"]
    assert not result.get("plan")


async def test_l3_readiness_gate_refuses_before_any_plan(tmp_path: Path) -> None:
    recipe = Recipe(
        name="l3-recipe",
        description="tier L3, no readiness satisfied",
        inputs=[],
        steps=[RecipeStep(id="a", capability="x.some.step", args={})],
        source_path=tmp_path / "l3-recipe.yaml",
        tier="L3",
    )
    result = await run_recipe(recipe, {}, tmp_path)

    assert result["success"] is False
    assert "readiness" in result
    assert not result.get("plan")


async def test_run_recipe_never_reports_success_for_unexecuted_dispatch(tmp_path: Path) -> None:
    """`_call_tool` still exists (CapabilityValidator/node-gate depends on it),
    but `run_recipe` must never call it or launder its "unresolved" dispatch
    stub into a step-level success. A resolved-but-external step must land
    in `plan`, never in a step whose telemetry/output claims real execution.
    """
    _write_capabilities(tmp_path, {"firecrawl.scrape_url": "x.no.dispatch"})
    recipe = Recipe(
        name="no-dispatch",
        description="single bound-external step",
        inputs=[],
        steps=[RecipeStep(id="a", capability="x.no.dispatch", args={})],
        source_path=tmp_path / "no-dispatch.yaml",
    )
    result = await run_recipe(recipe, {}, tmp_path)

    assert result["success"] is True
    assert result["plan"] == [{
        "step_id": "a",
        "capability": "x.no.dispatch",
        "resolved_mcp": "firecrawl",
        "resolved_tool": "scrape_url",
        "args": {},
    }]
    # never the old dispatch-stub shape laundered into a step output
    assert result["outputs"]["a"].get("status") != "unresolved"
