"""Recipe loader — reads YAML recipe files from three scopes:

  1. bundled: src/vise/assets/recipes/*.yaml (shipped with vise)
  2. user:    ~/.vise/recipes/*.yaml
  3. project: <project_dir>/.vise/recipes/*.yaml  (highest precedence)

When the same recipe ``name`` appears in multiple scopes, the higher-precedence
one fully replaces the lower (no per-step merging).

Also loads capability assignments from <project>/.vise/capabilities.yaml
and user pins from <project>/.vise/recipe-defaults.yaml.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # pyyaml

import vise
from vise.recipes.capabilities import validate_capability

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RecipeStep:
    id: str
    capability: str
    args: dict[str, object] = field(default_factory=dict)
    description: str = ""


@dataclass
class Recipe:
    name: str
    description: str
    inputs: list[str]
    steps: list[RecipeStep]
    source_path: Path
    scope: str = "project"  # "bundled" | "user" | "project"
    tier: str | None = None  # "L1" | "L2" | "L3" | None (no enforcement)
    cadence: str | None = None  # documentation for external cron/systemd caller, e.g. "1d", "*/30 * * * *"
    cost: int | None = None     # estimated token budget for D cost gate (integer)


@dataclass
class CapabilityAssignment:
    """Maps mcp_name.tool_name -> capability string."""
    tool: str          # e.g. "firecrawl.scrape_url"
    capability: str    # e.g. "web.scrape"


# ---------------------------------------------------------------------------
# YAML loading helpers
# ---------------------------------------------------------------------------

def _parse_recipe(data: dict, source_path: Path, scope: str = "project") -> Recipe:
    """Parse a raw YAML dict into a Recipe.  Raises ValueError on bad schema."""
    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ValueError(f"recipe at {source_path} missing required 'name' field")

    steps_raw = data.get("steps")
    if not steps_raw or not isinstance(steps_raw, list):
        raise ValueError(f"recipe '{name}' at {source_path} missing 'steps' list")

    steps: list[RecipeStep] = []
    for i, raw in enumerate(steps_raw):
        if not isinstance(raw, dict):
            raise ValueError(f"recipe '{name}' step {i} is not a mapping")
        step_id = raw.get("id")
        cap = raw.get("capability")
        if not step_id:
            raise ValueError(f"recipe '{name}' step {i} missing 'id'")
        if not cap:
            raise ValueError(f"recipe '{name}' step {i} missing 'capability'")
        validate_capability(str(cap))
        steps.append(RecipeStep(
            id=str(step_id),
            capability=str(cap),
            args=raw.get("args", {}),
            description=raw.get("description", ""),
        ))

    # Parse tier (optional; None means no enforcement — backward-compatible).
    tier_raw = data.get("tier")
    tier: str | None = None
    if tier_raw is not None:
        from vise.recipes.tiers import VALID_TIERS
        tier_str = str(tier_raw).upper()
        if tier_str not in VALID_TIERS:
            log.warning(
                "[recipes] recipe '%s' has unknown tier %r — ignoring (no enforcement)",
                name, tier_raw,
            )
        else:
            tier = tier_str

    # Parse cadence (optional; metadata string for external cron/systemd caller).
    cadence_raw = data.get("cadence")
    cadence: str | None = str(cadence_raw) if cadence_raw is not None else None

    # Parse cost (optional; estimated token budget integer for D cost gate).
    cost_raw = data.get("cost")
    cost: int | None = None
    if cost_raw is not None:
        try:
            cost = int(cost_raw)
        except (TypeError, ValueError):
            log.warning(
                "[recipes] recipe '%s' has non-integer cost %r — ignoring",
                name, cost_raw,
            )

    return Recipe(
        name=str(name),
        description=data.get("description", ""),
        inputs=list(data.get("inputs", [])),
        steps=steps,
        source_path=source_path,
        scope=scope,
        tier=tier,
        cadence=cadence,
        cost=cost,
    )


def _resolve_recipe_dirs(project_dir: str | Path) -> list[tuple[str, Path]]:
    """Return scope dirs in ascending precedence order (lowest first).

    Returns list of ``(scope_name, path)`` tuples.  Missing dirs are included;
    callers skip them if they don't exist.
    """
    bundled_dir = Path(vise.__file__).parent / "assets" / "recipes"
    user_dir = Path("~/.vise/recipes").expanduser()
    project_recipes_dir = Path(project_dir) / ".vise" / "recipes"
    return [
        ("bundled", bundled_dir),
        ("user", user_dir),
        ("project", project_recipes_dir),
    ]


def _load_recipes_from_dir(recipes_dir: Path, scope: str) -> dict[str, Recipe]:
    """Load all *.yaml files from a directory into a name->Recipe dict."""
    result: dict[str, Recipe] = {}
    if not recipes_dir.is_dir():
        return result
    for yaml_path in sorted(recipes_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                log.warning("[recipes] %s: top-level is not a mapping, skipping", yaml_path)
                continue
            recipe = _parse_recipe(raw, yaml_path, scope)
            result[recipe.name] = recipe
        except ValueError as e:
            log.error("[recipes] skipping %s: %s", yaml_path, e)
        except yaml.YAMLError as e:
            log.error("[recipes] YAML parse error in %s: %s", yaml_path, e)
    return result


def load_recipes(project_dir: str | Path) -> list[Recipe]:
    """Load recipes from all three scopes, merging by name (project wins)."""
    merged: dict[str, Recipe] = {}
    for scope, recipes_dir in _resolve_recipe_dirs(project_dir):
        merged.update(_load_recipes_from_dir(recipes_dir, scope))
    return list(merged.values())


def load_capabilities(project_dir: str | Path) -> dict[str, str]:
    """Load capability assignments from <project_dir>/.vise/capabilities.yaml.

    Returns a dict of ``tool -> capability`` (e.g. ``"firecrawl.scrape_url" -> "web.scrape"``).
    """
    cap_path = Path(project_dir) / ".vise" / "capabilities.yaml"
    if not cap_path.exists():
        return {}
    try:
        raw = yaml.safe_load(cap_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            log.warning("[recipes] capabilities.yaml is not a mapping")
            return {}
        assignments: dict[str, str] = {}
        for tool, cap in raw.items():
            if cap is None:
                continue
            assignments[str(tool)] = str(cap)
        return assignments
    except yaml.YAMLError as e:
        log.error("[recipes] YAML parse error in capabilities.yaml: %s", e)
        return {}


def load_user_pins(project_dir: str | Path) -> dict[str, str]:
    """Load user pins from <project_dir>/.vise/recipe-defaults.yaml.

    Returns a dict of ``capability -> mcp_name.tool_name`` overrides.
    """
    pins_path = Path(project_dir) / ".vise" / "recipe-defaults.yaml"
    if not pins_path.exists():
        return {}
    try:
        raw = yaml.safe_load(pins_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    except yaml.YAMLError as e:
        log.error("[recipes] YAML parse error in recipe-defaults.yaml: %s", e)
        return {}
