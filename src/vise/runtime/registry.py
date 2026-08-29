"""Who can do this kind of work — see docs/agent-runtime.md.

vise already ships 20 agent charters under ``agents/``, each carrying the model,
effort, tool set and preloaded skills that agent runs with. The registry reads
those files; it does not restate them. A second list of agents maintained
alongside the charters would drift from them, and the drift would be invisible
until a run routed work to a model the charter had already moved off.

Three facts the runtime needs are not in the charters today, so they are derived
and may be overridden by explicit frontmatter:

``role``       what kind of work this agent does — derived from the agent name.
``writes``     whether it may modify the tree — derived from its tool list. An
               agent with neither ``Write`` nor ``Edit`` cannot write, whatever
               it says about itself, so the tools are the truth.
``capabilities`` what it knows — derived from the name suffix and the ``*-rules``
               skills it preloads.

Derivation is pinned by ``test_runtime_registry.py``: every bundled charter must
resolve to a known role, or the registry has silently stopped being able to route
to it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Tools whose presence means an agent can modify the working tree.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

#: Exact agent-name to role. Anything not here falls through to the prefix rule
#: below; anything that falls through *that* has no role and cannot be routed to.
_ROLE_BY_NAME: dict[str, str] = {
    "tester": "test",
    "reviewer": "review",
    "debugger": "debug",
    "designer": "design",
    "docs-writer": "docs",
    "db-migrator": "migration",
    "security-auditor": "security",
    "frontend": "frontend",
}

_ROLE_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("backend-", "backend"),
    ("frontend-", "frontend"),
)


class RegistryError(Exception):
    """Raised when a charter cannot be read as an agent.

    Fails closed. A charter that will not parse is not skipped with a warning:
    the run that would have used it would instead route to some other agent and
    look like it worked.
    """


@dataclass(frozen=True)
class Resolution:
    """The outcome of asking the registry for an agent.

    Carries *why* when it carries no agent. A planner that only learns "None"
    can report that a task is unroutable but not what would make it routable,
    and "no agent for role backend" is a materially worse message than "12
    agents take it; name a language in the task".
    """

    agent: AgentSpec | None
    ambiguous_among: tuple[str, ...] = ()
    reason: str = ""

    def __bool__(self) -> bool:
        return self.agent is not None


@dataclass(frozen=True)
class AgentSpec:
    """One registered agent, as the runtime needs to see it."""

    id: str
    role: str | None
    description: str
    model: str | None = None
    effort: str | None = None
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    writes: bool = True
    parallel: bool = True
    charter: str = ""

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "model": self.model,
            "effort": self.effort,
            "writes": self.writes,
            "parallel": self.parallel,
            "capabilities": list(self.capabilities),
        }


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise RegistryError(f"{path.name}: no frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise RegistryError(f"{path.name}: unterminated frontmatter")
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path.name}: frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError(f"{path.name}: frontmatter is not a mapping")
    return data


def _as_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(v.strip() for v in value.split(",") if v.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return ()


def derive_role(name: str) -> str | None:
    """Role for an agent name, or None when nothing claims it."""
    if name in _ROLE_BY_NAME:
        return _ROLE_BY_NAME[name]
    for prefix, role in _ROLE_BY_PREFIX:
        if name.startswith(prefix):
            return role
    return None


def derive_capabilities(name: str, role: str | None, skills: tuple[str, ...]) -> tuple[str, ...]:
    """What this agent knows: its role, its language, and its rule sets."""
    caps: list[str] = []
    if role:
        caps.append(role)
    for prefix, _ in _ROLE_BY_PREFIX:
        if name.startswith(prefix):
            caps.append(name[len(prefix):])
    for skill in skills:
        if skill.endswith("-rules"):
            caps.append(skill[: -len("-rules")])
    seen: dict[str, None] = {}
    for c in caps:
        seen.setdefault(c, None)
    return tuple(seen)


def load_agent(path: Path) -> AgentSpec:
    """Read one charter into an AgentSpec. Raises RegistryError on anything odd."""
    fm = _frontmatter(path)
    name = str(fm.get("name") or path.stem)
    description = str(fm.get("description") or "")
    if not description:
        raise RegistryError(f"{path.name}: missing description — nothing can route to it")
    tools = _as_list(fm.get("tools"))
    skills = _as_list(fm.get("skills"))
    role = str(fm["role"]) if fm.get("role") else derive_role(name)
    declared_writes = fm.get("writes")
    # The tool list wins over a declaration. An agent claiming writes=true with
    # no write tool would be admitted against ownership it cannot use, and hold
    # a claim that blocks a task that could actually do the work.
    can_write = bool(_WRITE_TOOLS & set(tools)) if tools else True
    writes = can_write if declared_writes is None else (bool(declared_writes) and can_write)
    capabilities = _as_list(fm.get("capabilities")) or derive_capabilities(name, role, skills)
    return AgentSpec(
        id=name,
        role=role,
        description=description,
        model=(str(fm["model"]) if fm.get("model") else None),
        effort=(str(fm["effort"]) if fm.get("effort") else None),
        tools=tools,
        skills=skills,
        capabilities=capabilities,
        writes=writes,
        parallel=bool(fm.get("parallel", True)),
        charter=str(path),
    )


def bundled_agents_dir() -> Path | None:
    """The ``agents/`` directory of the installed plugin, if we are inside one."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            candidate = parent / "agents"
            return candidate if candidate.is_dir() else None
    return None


@dataclass
class AgentRegistry:
    """The agents a run may route to."""

    agents: dict[str, AgentSpec] = field(default_factory=dict)

    @classmethod
    def from_dir(cls, directory: Path) -> AgentRegistry:
        reg = cls()
        for path in sorted(Path(directory).glob("*.md")):
            spec = load_agent(path)
            reg.agents[spec.id] = spec
        return reg

    @classmethod
    def bundled(cls) -> AgentRegistry:
        """The registry from the installed plugin, or an empty one.

        Empty rather than raising: a standalone (non-plugin) install has no
        ``agents/`` directory, and the caller that needs an agent will fail with
        a routing error naming the role — which is a better message than an
        import-time crash about a missing path.
        """
        directory = bundled_agents_dir()
        return cls.from_dir(directory) if directory else cls()

    def get(self, agent_id: str) -> AgentSpec | None:
        return self.agents.get(agent_id)

    def roles(self) -> set[str]:
        return {a.role for a in self.agents.values() if a.role}

    def for_role(
        self,
        role: str,
        *,
        writes: bool | None = None,
        capability: str | None = None,
    ) -> list[AgentSpec]:
        """Every agent that can take this role, most specific first.

        Sorted by capability count descending then id, so ``backend-python``
        outranks a hypothetical generic backend agent for a Python task while
        the order stays deterministic between runs.
        """
        out = [a for a in self.agents.values() if a.role == role]
        if writes is not None:
            out = [a for a in out if a.writes == writes]
        if capability:
            out = [a for a in out if a.can(capability)]
        return sorted(out, key=lambda a: (-len(a.capabilities), a.id))

    def select(
        self,
        role: str,
        *,
        writes: bool | None = None,
        capability: str | None = None,
    ) -> AgentSpec | None:
        """The first qualifying agent, or None. Convenience over ``resolve``."""
        return self.resolve(role, writes=writes, capability=capability).agent

    def resolve(
        self,
        role: str,
        *,
        writes: bool | None = None,
        capability: str | None = None,
    ) -> Resolution:
        """Pick the one agent for this work, or explain why there isn't one.

        Ambiguity is reported, not broken alphabetically. Twelve agents take the
        ``backend`` role and they differ by language; picking whichever sorts
        first sends a Python task to the C++ charter and the plan reads as though
        someone chose that. A tie that no capability discriminates is a hole in
        the task, and the fix is in the task.
        """
        candidates = self.for_role(role, writes=writes, capability=capability)
        if not candidates and capability:
            # Falling back is right: a task naming a language nobody registered
            # should run on whatever does take the role rather than not run.
            candidates = self.for_role(role, writes=writes)
            if len(candidates) == 1:
                return Resolution(
                    candidates[0],
                    reason=f"no agent carries '{capability}'; {candidates[0].id} is the "
                           f"only one taking role '{role}'",
                )
        if not candidates:
            return Resolution(None, reason=f"no registered agent takes role '{role}'")
        if len(candidates) == 1:
            return Resolution(candidates[0])
        # Several qualify. Only a strictly richer capability set breaks the tie —
        # anything else is coincidence dressed as a decision.
        top = len(candidates[0].capabilities)
        tied = [a for a in candidates if len(a.capabilities) == top]
        if len(tied) == 1:
            return Resolution(tied[0])
        return Resolution(
            None,
            ambiguous_among=tuple(a.id for a in tied),
            reason=(
                f"{len(tied)} agents take role '{role}' and nothing in the task "
                f"distinguishes them"
            ),
        )
