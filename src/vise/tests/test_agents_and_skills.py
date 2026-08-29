"""Agent + skill frontmatter must be valid — a typo here breaks subagent launch
or silently fails to preload a skill, and neither shows up until runtime.

Guards: valid model/effort/color enums, every `tools` entry resolves, and every
`skills:` reference points at a skill that actually ships in this repo.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO / "agents"
SKILLS_DIR = REPO / "skills"

AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))

# Built-in Claude Code tools an agent may list. MCP tools (mcp__*) are allowed
# too; anything else is almost certainly a typo (docs: an unresolved tool entry
# fails the subagent at launch).
BUILTIN_TOOLS = {
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Glob", "Grep", "Bash", "BashOutput", "KillShell",
    "Task", "WebFetch", "WebSearch", "TodoWrite", "Skill",
    # LSP is the host's code-intelligence tool — the consumer of the language
    # servers vise declares in plugin.json. It belongs here because this set
    # exists to catch names that will not resolve at launch, and this one does.
    "LSP",
}
VALID_MODELS = {"sonnet", "opus", "haiku", "fable", "inherit"}
VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}
VALID_COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}

# Skills that ship in this repo — the only ones an agent may preload by name.
LOCAL_SKILLS = {p.parent.name for p in SKILL_FILES}


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path.name}: missing frontmatter"
    _, fm, _ = text.split("---", 2)
    data = yaml.safe_load(fm)
    assert isinstance(data, dict), f"{path.name}: frontmatter is not a mapping"
    return data


def test_agents_dir_has_files():
    assert AGENT_FILES, f"no agent .md found in {AGENTS_DIR}"


def test_skills_dir_has_files():
    assert SKILL_FILES, f"no SKILL.md found under {SKILLS_DIR}"


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.name)
def test_agent_frontmatter_valid(path: Path):
    fm = _frontmatter(path)

    assert fm.get("name"), f"{path.name}: missing name"
    assert fm.get("description"), f"{path.name}: missing description"

    model = fm.get("model")
    if model is not None:
        assert model in VALID_MODELS or str(model).startswith("claude-"), \
            f"{path.name}: invalid model {model!r}"

    effort = fm.get("effort")
    if effort is not None:
        assert effort in VALID_EFFORT, f"{path.name}: invalid effort {effort!r}"

    color = fm.get("color")
    if color is not None:
        assert color in VALID_COLORS, f"{path.name}: invalid color {color!r}"

    tools = fm.get("tools")
    if tools is not None:
        names = tools if isinstance(tools, list) else [t.strip() for t in str(tools).split(",")]
        for t in names:
            assert t in BUILTIN_TOOLS or t.startswith("mcp__"), \
                f"{path.name}: unknown tool {t!r} (typo? won't resolve at launch)"

    for skill in fm.get("skills") or []:
        assert skill in LOCAL_SKILLS, \
            f"{path.name}: references skill {skill!r} not shipped in {SKILLS_DIR}"


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_frontmatter_valid(path: Path):
    fm = _frontmatter(path)
    assert fm.get("name"), f"{path.parent.name}: missing name"
    assert fm.get("description"), f"{path.parent.name}: missing description"
    assert fm["name"] == path.parent.name, \
        f"{path.parent.name}: skill name {fm['name']!r} != directory name"


def test_every_agent_resolves_to_a_runtime_role():
    """A charter the runtime cannot route to still ships, still loads in Claude
    Code, and is simply never picked. Nothing else in the suite notices."""
    from vise.runtime.registry import derive_role

    unrouted = [
        p.name for p in AGENT_FILES
        if not (_frontmatter(p).get("role") or derive_role(_frontmatter(p)["name"]))
    ]
    assert not unrouted, f"agents no task can reach: {unrouted}"


def test_claude_md_states_the_real_agent_count():
    """Prose restating a fact drifts from it. This is the fact."""
    import re

    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"\|\s*`agents/`\s*\|\s*(\d+) bundled subagent charters", text)
    assert match, "CLAUDE.md no longer states an agent count in the layout table"
    assert int(match.group(1)) == len(AGENT_FILES), (
        f"CLAUDE.md says {match.group(1)} agents; {len(AGENT_FILES)} ship"
    )
