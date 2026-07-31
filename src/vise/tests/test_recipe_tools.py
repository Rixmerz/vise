"""The MCP tool surface for recipes (`vise.tools.recipes`), driven through
the real registered tools rather than the module-level helper functions —
same fake-MCP capture harness used by test_graph_deactivate.py /
test_node_gate_traverse.py, since the tools are defined inside
``register_recipes``'s closure and unreachable by direct import.

The central contract pinned here: vise is an MCP *server* and cannot
dispatch another server's tool, so a fully-bound `recipe_run` returns
`success=True` with a non-empty `plan` — it resolved the recipe, it did not
execute it. A test that only checks `success is True` would be a false
green for that distinction, so the plan's non-emptiness and its "execute
this yourself" framing are asserted directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

RECIPE_YAML = """\
name: {name}
description: a project-local test recipe
inputs: []
steps:
  - id: step-one
    capability: {capability}
    description: a single step
    args:
      url: "https://example.test"
"""


@pytest.fixture
def tools():
    """All tools registered by ``register_recipes``, captured via a fake MCP."""
    registered: dict = {}

    class _FakeMCP:
        def tool(self, *a, **kw):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    from vise.tools.recipes import register_recipes

    register_recipes(_FakeMCP())
    return registered


def _write_recipe(project_dir: Path, name: str, capability: str) -> None:
    recipes_dir = project_dir / ".vise" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    (recipes_dir / f"{name}.yaml").write_text(
        RECIPE_YAML.format(name=name, capability=capability), encoding="utf-8"
    )


def _bind(project_dir: Path, tool: str, capability: str) -> None:
    cap_dir = project_dir / ".vise"
    cap_dir.mkdir(parents=True, exist_ok=True)
    (cap_dir / "capabilities.yaml").write_text(
        yaml.dump({tool: capability}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# recipe_list
# ---------------------------------------------------------------------------

def test_recipe_list_marks_unbound_project_recipe_not_runnable(tools, tmp_path):
    _write_recipe(tmp_path, "unbound-probe", "x.probe.unbound")

    out = tools["recipe_list"](project_dir=str(tmp_path))

    row = next(r for r in out["recipes"] if r["name"] == "unbound-probe")
    assert row["runnable"] is False
    assert row["unresolved_capabilities"] == 1
    assert row["all_capabilities_resolved"] is False


def test_recipe_list_marks_bound_project_recipe_runnable(tools, tmp_path):
    _write_recipe(tmp_path, "bound-probe", "x.probe.bound")
    _bind(tmp_path, "firecrawl.scrape_url", "x.probe.bound")

    out = tools["recipe_list"](project_dir=str(tmp_path))

    row = next(r for r in out["recipes"] if r["name"] == "bound-probe")
    assert row["runnable"] is True
    assert row["unresolved_capabilities"] == 0


def test_recipe_list_counts_include_bundled_recipes(tools, tmp_path):
    """Bundled recipes (in src/vise/assets/recipes) always load alongside
    project-local ones — count must reflect all three scopes, not just the
    project dir."""
    out = tools["recipe_list"](project_dir=str(tmp_path))

    names = {r["name"] for r in out["recipes"]}
    assert "deploy-and-validate" in names
    assert out["count"] == len(out["recipes"])


def test_recipe_list_sorts_runnable_recipes_before_unrunnable(tools, tmp_path):
    _write_recipe(tmp_path, "aaa-unbound", "x.zzz.unbound")
    _write_recipe(tmp_path, "zzz-bound", "x.aaa.bound")
    _bind(tmp_path, "firecrawl.scrape_url", "x.aaa.bound")

    out = tools["recipe_list"](project_dir=str(tmp_path))

    names_in_order = [r["name"] for r in out["recipes"]]
    assert names_in_order.index("zzz-bound") < names_in_order.index("aaa-unbound")


# ---------------------------------------------------------------------------
# recipe_describe
# ---------------------------------------------------------------------------

def test_recipe_describe_unknown_name_errors_cleanly(tools, tmp_path):
    out = tools["recipe_describe"](name="does-not-exist", project_dir=str(tmp_path))

    assert out["error"] == "recipe 'does-not-exist' not found"
    assert "steps" not in out


def test_recipe_describe_reports_unresolved_step_as_gap_not_silent_pass(tools, tmp_path):
    _write_recipe(tmp_path, "gap-probe", "x.gap.capability")

    out = tools["recipe_describe"](name="gap-probe", project_dir=str(tmp_path))

    step = out["steps"][0]
    assert step["capability"] == "x.gap.capability"
    assert step["resolved"] is False
    assert step["resolved_mcp"] is None
    assert step["resolved_tool"] is None


def test_recipe_describe_reports_resolved_step_mcp_and_tool(tools, tmp_path):
    _write_recipe(tmp_path, "resolved-probe", "x.resolved.capability")
    _bind(tmp_path, "firecrawl.scrape_url", "x.resolved.capability")

    out = tools["recipe_describe"](name="resolved-probe", project_dir=str(tmp_path))

    step = out["steps"][0]
    assert step["resolved"] is True
    assert step["resolved_mcp"] == "firecrawl"
    assert step["resolved_tool"] == "scrape_url"


# ---------------------------------------------------------------------------
# recipe_run — unknown / unbound / explain
# ---------------------------------------------------------------------------

async def test_recipe_run_unknown_name_errors_cleanly(tools, tmp_path):
    out = await tools["recipe_run"](name="does-not-exist", project_dir=str(tmp_path))

    assert out["success"] is False
    assert out["error"] == "recipe 'does-not-exist' not found"


async def test_recipe_run_unresolved_capability_surfaces_as_gap_with_fix(tools, tmp_path):
    """An unbound capability must halt with the exact capability_set(...) call
    needed — never a silent pass, never a generic per-step halt."""
    _write_recipe(tmp_path, "gap-run-probe", "x.gap.run.capability")

    out = await tools["recipe_run"](name="gap-run-probe", project_dir=str(tmp_path))

    assert out["success"] is False
    assert out["unresolved_capabilities"] == ["x.gap.run.capability"]
    assert len(out["capability_set_calls_needed"]) == 1
    assert 'capability="x.gap.run.capability"' in out["capability_set_calls_needed"][0]


async def test_recipe_run_explain_returns_chain_without_producing_a_plan(tools, tmp_path):
    _write_recipe(tmp_path, "explain-probe", "x.explain.capability")
    _bind(tmp_path, "firecrawl.scrape_url", "x.explain.capability")

    out = await tools["recipe_run"](
        name="explain-probe", explain=True, project_dir=str(tmp_path)
    )

    assert out["success"] is True
    assert out["explain"] is True
    assert out["resolution_chain"][0]["resolved_mcp"] == "firecrawl"
    assert "plan" not in out


# ---------------------------------------------------------------------------
# recipe_run — the false-green contract: success=True never means executed
# ---------------------------------------------------------------------------

async def test_recipe_run_fully_bound_returns_plan_it_did_not_execute(tools, tmp_path):
    """A fully-resolved recipe still returns an unexecuted PLAN — vise has no
    MCP dispatch layer. `success=True` here describes "resolved cleanly", not
    "ran". A test asserting only `success is True` would be a false green
    for the very bug class this repo has repeatedly fixed."""
    _write_recipe(tmp_path, "bound-run-probe", "x.bound.run.capability")
    _bind(tmp_path, "firecrawl.scrape_url", "x.bound.run.capability")

    out = await tools["recipe_run"](name="bound-run-probe", project_dir=str(tmp_path))

    assert out["success"] is True
    assert len(out["plan"]) == 1
    assert out["plan"][0]["resolved_mcp"] == "firecrawl"
    assert out["plan"][0]["resolved_tool"] == "scrape_url"
    assert "execute" in out["message"]


async def test_recipe_run_dry_run_still_reports_a_plan_not_execution(tools, tmp_path):
    _write_recipe(tmp_path, "dry-run-probe", "x.dry.run.capability")
    _bind(tmp_path, "firecrawl.scrape_url", "x.dry.run.capability")

    out = await tools["recipe_run"](
        name="dry-run-probe", dry_run=True, project_dir=str(tmp_path)
    )

    assert out["success"] is True
    assert out["dry_run"] is True
    assert len(out["plan"]) == 1


# ---------------------------------------------------------------------------
# capability_set
# ---------------------------------------------------------------------------

def test_capability_set_rejects_tool_without_dot(tools, tmp_path):
    out = tools["capability_set"](
        tool="not-namespaced", capability="x.some.cap", project_dir=str(tmp_path)
    )

    assert out["success"] is False
    assert "mcp_name.tool_name" in out["error"]


def test_capability_set_rejects_unknown_capability(tools, tmp_path):
    out = tools["capability_set"](
        tool="firecrawl.scrape_url", capability="not.in.taxonomy", project_dir=str(tmp_path)
    )

    assert out["success"] is False
    assert "taxonomy" in out["error"]


def test_capability_set_persists_assignment_to_disk(tools, tmp_path):
    out = tools["capability_set"](
        tool="firecrawl.scrape_url", capability="web.scrape", project_dir=str(tmp_path)
    )

    assert out["success"] is True
    assert out["action"] == "set"
    on_disk = yaml.safe_load(
        (tmp_path / ".vise" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    assert on_disk == {"firecrawl.scrape_url": "web.scrape"}


def test_capability_set_clears_an_existing_assignment(tools, tmp_path):
    tools["capability_set"](
        tool="firecrawl.scrape_url", capability="web.scrape", project_dir=str(tmp_path)
    )

    out = tools["capability_set"](
        tool="firecrawl.scrape_url", capability=None, project_dir=str(tmp_path)
    )

    assert out["success"] is True
    assert out["action"] == "cleared"
    on_disk = yaml.safe_load(
        (tmp_path / ".vise" / "capabilities.yaml").read_text(encoding="utf-8")
    )
    assert on_disk == {}


# ---------------------------------------------------------------------------
# capability_audit
# ---------------------------------------------------------------------------

def test_capability_audit_lists_unresolved_capability_with_owning_recipe(tools, tmp_path):
    _write_recipe(tmp_path, "audit-probe", "x.audit.capability")

    out = tools["capability_audit"](
        project_dir=str(tmp_path), include_low_confidence=False, include_conflicts=False
    )

    row = next(r for r in out["unresolved"] if r["capability"] == "x.audit.capability")
    assert row["used_in_recipes"] == ["audit-probe"]
    assert out["count"] == len(out["unresolved"])


def test_capability_audit_flags_conflict_between_pin_and_assignment(tools, tmp_path):
    _bind(tmp_path, "firecrawl.scrape_url", "web.scrape")
    pins_path = tmp_path / ".vise" / "recipe-defaults.yaml"
    pins_path.write_text(yaml.dump({"web.scrape": "otherproxy.fetch"}), encoding="utf-8")

    out = tools["capability_audit"](
        project_dir=str(tmp_path), include_low_confidence=False, include_conflicts=True
    )

    assert out["conflicts_count"] == 1
    assert out["conflicts"][0]["capability"] == "web.scrape"
    assert out["conflicts"][0]["user_pin"] == "otherproxy.fetch"
