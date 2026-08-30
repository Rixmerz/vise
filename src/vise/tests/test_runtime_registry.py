"""The registry reads the shipped charters; it must not drift from them.

The load-bearing test here is `test_every_bundled_agent_resolves_to_a_role`. An
agent whose name stops matching the derivation table silently becomes
unroutable — the charter still ships, still loads in Claude Code, and the runtime
simply never picks it. Nothing else in the suite would notice.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.runtime.registry import (
    AgentRegistry,
    RegistryError,
    bundled_agents_dir,
    capability_hint,
    derive_capabilities,
    derive_role,
    load_agent,
)

BUNDLED = bundled_agents_dir()
pytestmark = pytest.mark.skipif(BUNDLED is None, reason="not running from a plugin checkout")


def _registry() -> AgentRegistry:
    return AgentRegistry.from_dir(BUNDLED)


def test_every_bundled_agent_resolves_to_a_role():
    unrouted = [a.id for a in _registry().agents.values() if not a.role]
    assert not unrouted, f"agents the runtime can never route to: {unrouted}"


def test_registry_loads_every_charter_that_ships():
    assert len(_registry().agents) == len(list(BUNDLED.glob("*.md")))


def test_write_capability_comes_from_the_tool_list_not_a_claim():
    reg = _registry()
    assert reg.get("reviewer").writes is False, "reviewer holds no Write/Edit"
    assert reg.get("backend-python").writes is True


def test_a_declared_write_cannot_exceed_the_tool_list(tmp_path: Path):
    """An agent claiming writes it has no tool for would hold a claim it cannot use."""
    charter = tmp_path / "liar.md"
    charter.write_text(
        "---\nname: liar\ndescription: claims to write\nwrites: true\n"
        "tools: Read, Grep\n---\n\nbody\n",
        encoding="utf-8",
    )
    assert load_agent(charter).writes is False


def test_language_capability_is_derived_from_the_name_and_rules_skill():
    caps = _registry().get("backend-python").capabilities
    assert "backend" in caps and "python" in caps


def test_derive_capabilities_reads_rules_skills():
    assert "rust" in derive_capabilities("backend-rust", "backend", ("rust-rules",))


def test_derive_role_returns_none_for_an_unknown_name():
    assert derive_role("some-new-agent") is None


def test_resolve_picks_the_only_agent_for_a_role():
    assert _registry().resolve("review").agent.id == "reviewer"


def test_resolve_reports_ambiguity_rather_than_choosing_alphabetically():
    """12 backend agents differ by language; picking the first sorts a Python
    task onto the C++ charter and the plan reads as if someone chose that."""
    resolution = _registry().resolve("backend")
    assert resolution.agent is None
    assert len(resolution.ambiguous_among) > 1
    assert "distinguishes" in resolution.reason


def test_a_capability_breaks_the_tie():
    assert _registry().resolve("backend", capability="python").agent.id == "backend-python"


def test_an_unregistered_capability_falls_back_to_the_role():
    """A task naming a language nobody registered should still run."""
    resolution = _registry().resolve("test", capability="cobol")
    assert resolution.agent.id == "tester"
    assert "cobol" in resolution.reason


def test_resolve_explains_an_unknown_role():
    resolution = _registry().resolve("astrology")
    assert resolution.agent is None
    assert "astrology" in resolution.reason


def test_a_charter_without_frontmatter_fails_closed(tmp_path: Path):
    bad = tmp_path / "bad.md"
    bad.write_text("no frontmatter here\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        load_agent(bad)


def test_a_charter_without_a_description_fails_closed(tmp_path: Path):
    """Description is what routes work to an agent. Without it nothing can."""
    bad = tmp_path / "bad.md"
    bad.write_text("---\nname: bad\n---\n\nbody\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        load_agent(bad)


def test_an_empty_registry_answers_rather_than_raising():
    reg = AgentRegistry()
    assert reg.select("backend") is None
    assert reg.roles() == set()


# --- capability hint ------------------------------------------------------


class _Task:
    def __init__(self, id: str, name: str = ""):
        self.id = id
        self.name = name


def test_a_capability_survives_any_punctuation_around_it():
    """Found by writing a workflow by hand.

    The tokeniser split on a hand-written list of separators, so a task named
    "Money value type (python)" tokenised to "(python)" and matched nothing.
    Every backend task in that workflow came back UNROUTABLE, with an error
    telling the author to name the capability — which they had.
    """
    for name in ("Money value type (python)", "parser [python]", "report — python",
                 "cli/python", "tests_python", "docs: python!"):
        assert capability_hint(_Task("t", name)) == "python", name


def test_the_id_is_read_as_well_as_the_name():
    assert capability_hint(_Task("backend-python-auth")) == "python"


def test_a_task_naming_no_capability_gets_no_hint():
    assert capability_hint(_Task("money", "Money value type")) is None


def test_a_subject_that_is_not_a_language_is_not_a_hint():
    """"parser" and "ledger" are subjects; only registered languages route."""
    assert capability_hint(_Task("parser", "Ledger file parser")) is None
