"""A project can staff its own roles — see openspec/changes/project-local-agents.

`AgentRegistry.bundled()` read exactly one directory, so a project could not add
a capability without forking vise. That was already binding on vise itself:
seven of the fourteen roles the model policy prices resolve to nobody, including
every role on the cheapest tier.

The tests here hold two halves. The obvious one is that a project's charters
load. The one that matters more is that they are held to the *same* bar as the
bundled fleet — a second, laxer standard applied to the files nobody reviewed is
the whole risk of the feature.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.runtime.registry import (
    AgentRegistry,
    AgentSpec,
    bundled_agents_dir,
    validate_charter,
)
from vise.runtime.routing import POLICY

BUNDLED = bundled_agents_dir()
pytestmark = pytest.mark.skipif(BUNDLED is None, reason="not running from a plugin checkout")

CHARTER = """---
name: {name}
description: {description}
model: {model}
effort: low
role: {role}
tools: {tools}
---

Body.
"""


def _project(tmp_path: Path, **charters: str) -> Path:
    root = tmp_path / "proj"
    agents = root / ".vise" / "agents"
    agents.mkdir(parents=True)
    for name, text in charters.items():
        (agents / f"{name}.md").write_text(text, encoding="utf-8")
    return root


def _charter(name: str, role: str, *, model: str = "haiku",
             tools: str = "Read, Glob, Grep", description: str = "") -> str:
    return CHARTER.format(
        name=name, role=role, model=model, tools=tools,
        description=description or f"Does {role} work. Use when a task declares {role}.",
    )


# --- loading --------------------------------------------------------------


def test_a_project_can_staff_a_role_nobody_bundled(tmp_path: Path):
    """`research` is one of the seven the policy prices and the fleet cannot take."""
    assert AgentRegistry.bundled().resolve("research").agent is None
    root = _project(tmp_path, researcher=_charter("researcher", "research"))
    resolved = AgentRegistry.for_project(root).resolve("research")
    assert resolved.agent is not None
    assert resolved.agent.id == "researcher"


def test_a_project_with_no_agents_directory_is_unchanged(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert set(AgentRegistry.for_project(plain).agents) == set(AgentRegistry.bundled().agents)


def test_an_agents_path_that_is_a_file_is_not_fatal(tmp_path: Path):
    root = tmp_path / "proj"
    (root / ".vise").mkdir(parents=True)
    (root / ".vise" / "agents").write_text("not a directory\n", encoding="utf-8")
    reg = AgentRegistry.for_project(root)
    assert reg.agents, "the bundled fleet survives"


def test_a_missing_project_dir_is_not_fatal(tmp_path: Path):
    reg = AgentRegistry.for_project(tmp_path / "does-not-exist")
    assert reg.agents


# --- shadowing ------------------------------------------------------------


def test_a_project_charter_shadows_a_bundled_one(tmp_path: Path):
    root = _project(tmp_path, **{
        "docs-writer": _charter("docs-writer", "docs", model="haiku"),
    })
    reg = AgentRegistry.for_project(root)
    assert reg.resolve("docs").agent.model == "haiku"
    assert AgentRegistry.bundled().agents["docs-writer"].model != "haiku", \
        "the bundled charter is untouched"


def test_shadowing_is_recorded_rather_than_silent(tmp_path: Path):
    """The mechanism is allowed; the invisibility is not."""
    root = _project(tmp_path, **{"docs-writer": _charter("docs-writer", "docs")})
    reg = AgentRegistry.for_project(root)
    assert reg.shadowed == ("docs-writer",)
    assert reg.origins["docs-writer"] == "project"


def test_an_agent_nobody_shadows_keeps_its_origin(tmp_path: Path):
    root = _project(tmp_path, researcher=_charter("researcher", "research"))
    reg = AgentRegistry.for_project(root)
    assert reg.shadowed == ()
    assert reg.origins["researcher"] == "project"
    assert reg.origins["docs-writer"] == "bundled"


# --- the shared bar -------------------------------------------------------


def test_a_charter_naming_a_tool_that_does_not_exist_is_refused(tmp_path: Path):
    root = _project(tmp_path, ghost=_charter("ghost", "research", tools="Read, Telepathy"))
    reg = AgentRegistry.for_project(root)
    assert "ghost" not in reg.agents
    assert any("Telepathy" in reason for _, reason in reg.refused)


def test_a_charter_with_no_description_is_refused(tmp_path: Path):
    root = tmp_path / "proj"
    agents = root / ".vise" / "agents"
    agents.mkdir(parents=True)
    (agents / "mute.md").write_text("---\nname: mute\nrole: research\n---\n", encoding="utf-8")
    reg = AgentRegistry.for_project(root)
    assert "mute" not in reg.agents
    assert reg.refused


def test_an_invalid_model_or_effort_is_refused(tmp_path: Path):
    assert validate_charter(AgentSpec(id="a", role="r", description="d", model="gpt-9"))
    assert validate_charter(AgentSpec(id="a", role="r", description="d", effort="turbo"))


def test_a_skill_that_does_not_ship_is_refused():
    problems = validate_charter(
        AgentSpec(id="a", role="r", description="d", skills=("no-such-skill",))
    )
    assert any("does not ship" in p for p in problems)


def test_every_bundled_charter_passes_the_bar_it_imposes_on_projects():
    """One standard. A laxer check for unreviewed files would be backwards."""
    failing = {
        agent_id: validate_charter(spec)
        for agent_id, spec in AgentRegistry.bundled().agents.items()
        if validate_charter(spec)
    }
    assert not failing, failing


# --- one bad charter costs one agent --------------------------------------


def test_a_malformed_charter_beside_a_valid_one(tmp_path: Path):
    root = _project(
        tmp_path,
        researcher=_charter("researcher", "research"),
        rubbish="no frontmatter at all\n",
    )
    reg = AgentRegistry.for_project(root)
    assert reg.resolve("research").agent is not None, "the good one loaded"
    assert any("rubbish" in path for path, _ in reg.refused)


def test_a_refusal_carries_its_reason(tmp_path: Path):
    root = _project(tmp_path, ghost=_charter("ghost", "research", tools="Read, Telepathy"))
    _, reason = AgentRegistry.for_project(root).refused[0]
    assert reason, "a refusal nobody can act on is a silent drop"


# --- the drift between what the policy prices and what the fleet can take --


#: Roles the model policy prices that no bundled agent takes. Each is priced and
#: unstaffed, so a task declaring one is unroutable until a project supplies the
#: charter — which is what `.vise/agents/` is for.
#:
#: This list is a statement of the current gap, not permission for it to grow.
#: A new priced role must either ship an agent or be added here on purpose.
PROJECT_SUPPLIED_ROLES = {
    "research": "cheapest tier; a project's sources differ too much to bundle one",
    "extract": "cheapest tier; shape of the extraction is project-specific",
    "inventory": "cheapest tier; what counts as inventory is project-specific",
    "classify": "cheapest tier; the taxonomy is project-specific",
    "integration": "what 'integrated' means is a property of the deployment",
    "architecture": "the `architecture` skill exists; no agent carries it yet",
    "replan": "the scheduler's replanner hook is unwired, so nothing calls it",
}


def test_every_priced_role_is_staffed_or_declared_unstaffed():
    """The policy table and the fleet are two statements about the same thing.

    Nothing pinned them together, and they drifted: seven of fourteen priced
    roles resolve to nobody. Pinning them here means the next role added to the
    policy either ships an agent or says out loud that it does not.
    """
    bundled = AgentRegistry.bundled()
    # `roles()`, not `resolve()`. A role several agents take resolves to None
    # too — `backend` is taken by twelve — but that is ambiguity, not absence,
    # and the fix for it is in the task, not in the fleet. Reading the two as
    # the same thing is what would send someone to write a thirteenth backend
    # agent for a task that had a perfectly good twelfth.
    unstaffed = set(POLICY) - bundled.roles()
    undeclared = unstaffed - set(PROJECT_SUPPLIED_ROLES)
    assert not undeclared, (
        f"priced by POLICY, staffed by nobody, and not declared project-supplied: "
        f"{sorted(undeclared)}"
    )


def test_the_declared_gap_does_not_outlive_the_agent_that_closes_it():
    """A role that gains a bundled agent must leave the list, or the list stops
    describing anything."""
    staffed = AgentRegistry.bundled().roles()
    stale = {role for role in PROJECT_SUPPLIED_ROLES if role in staffed}
    assert not stale, f"now staffed; remove from PROJECT_SUPPLIED_ROLES: {sorted(stale)}"
