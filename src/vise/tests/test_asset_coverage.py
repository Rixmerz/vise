"""Every bundled rule has to be reachable, and every agent has to carry the floor.

`test_agents_and_skills.py` checks that frontmatter is *well-formed* — valid
enums, resolvable tools, `skills:` entries that exist. It says nothing about
whether the assets fit together, and that is where the real drift was:

  1. `swift-rules` and `lua-rules` shipped for months with no agent preloading
     them and no mention in the orchestration skill's fleet table. The LSP
     servers for both were configured in plugin.json, so the setup *looked*
     complete. Swift work routed to `general-purpose`, which preloads nothing,
     and the rules never once applied.
  2. Five code-touching agents (`tester`, `debugger`, `db-migrator`, `reviewer`,
     `security-auditor`) carried `ponytail` and no language rules at all — the
     whole conventions layer reached 11 of 17 agents.
  3. The ten `backend-*` charters drifted apart: two carried a
     "validate input / parameterize queries" line and the other eight did not,
     for the same role.

None of that is a typo, so no frontmatter check catches it. These are the
structural invariants instead.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO / "agents"
SKILLS_DIR = REPO / "skills"
ORCHESTRATION = SKILLS_DIR / "orchestration" / "SKILL.md"

AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))
RULES_SKILLS = sorted(p.parent.name for p in SKILLS_DIR.glob("*-rules/SKILL.md"))
BACKEND_AGENTS = sorted(AGENTS_DIR.glob("backend-*.md"))

# The floor every agent carries. An agent without it is running without vise's
# general rules AND without the precedence rule that settles conflicts between
# the project's conventions, a *-rules skill, and ponytail.
BASELINE = "engineering-baseline"

# Agents that write or review code, and therefore need language conventions —
# either preloaded in frontmatter or loaded on demand via the `Skill` tool.
# docs-writer is the one deliberate exclusion: it edits prose, not code.
NON_CODE_AGENTS = {"docs-writer"}

# Charters over this are unreadable and stop being followed; the autoheal skill
# enforces the same number when it decides between a charter edit and a runbook.
MAX_CHARTER_LINES = 150


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path.name}: missing frontmatter"
    _, fm, _ = text.split("---", 2)
    return yaml.safe_load(fm)


def _skills_of(path: Path) -> list[str]:
    return list(_frontmatter(path).get("skills") or [])


def _tools_of(path: Path) -> list[str]:
    tools = _frontmatter(path).get("tools")
    if tools is None:
        return []
    if isinstance(tools, list):
        return tools
    return [t.strip() for t in str(tools).split(",")]


# ---------------------------------------------------------------------------
# The baseline reaches everyone
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_every_agent_preloads_the_baseline(path: Path):
    assert BASELINE in _skills_of(path), (
        f"{path.name} does not preload {BASELINE!r}. Without it the agent has no "
        "general rules and no precedence rule — when the project's conventions "
        "and a *-rules skill disagree it picks whichever it read last."
    )


def test_the_baseline_skill_ships():
    assert (SKILLS_DIR / BASELINE / "SKILL.md").is_file(), (
        f"every agent preloads {BASELINE!r} but the skill itself is missing"
    )


# ---------------------------------------------------------------------------
# Language rules reach every agent that touches code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [p for p in AGENT_FILES if p.stem not in NON_CODE_AGENTS],
    ids=lambda p: p.stem,
)
def test_code_touching_agents_can_reach_language_rules(path: Path):
    """Either a `*-rules` skill is preloaded, or `Skill` is granted to load one.

    A language-agnostic agent (tester, debugger, reviewer) cannot preload
    fourteen rules skills, so it gets the `Skill` tool and a charter section
    telling it which one to load. An agent with neither writes code in a
    language whose conventions it was never shown.
    """
    preloaded = [s for s in _skills_of(path) if s.endswith("-rules")]
    if preloaded:
        return

    assert "Skill" in _tools_of(path), (
        f"{path.name} preloads no *-rules skill and cannot load one — it has no "
        "`Skill` tool. Either preload the rules for its language or grant `Skill`."
    )
    body = path.read_text(encoding="utf-8")
    assert "-rules" in body, (
        f"{path.name} has the `Skill` tool but its charter never names a "
        "*-rules skill to load, so nothing tells it which conventions apply."
    )


# ---------------------------------------------------------------------------
# No orphaned rules
# ---------------------------------------------------------------------------

def test_every_rules_skill_is_reachable():
    """A rules skill nobody preloads and nobody names is a file that never runs."""
    preloaded = {s for p in AGENT_FILES for s in _skills_of(p)}
    named_in_orchestration = ORCHESTRATION.read_text(encoding="utf-8")

    orphaned = [
        name for name in RULES_SKILLS
        if name not in preloaded and name not in named_in_orchestration
    ]
    assert not orphaned, (
        f"rules skills reachable by nothing: {orphaned}. Each is either preloaded "
        "by an agent or listed in the orchestration skill's general-purpose "
        "fallback — otherwise it ships and never applies to a single file."
    )


@pytest.mark.parametrize("name", RULES_SKILLS)
def test_every_rules_skill_points_at_the_precedence_rule(name: str):
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    assert BASELINE in text, (
        f"{name} states preferences but never points at {BASELINE}, so an agent "
        "reading it has no way to know the project's own conventions outrank it."
    )


@pytest.mark.parametrize("name", RULES_SKILLS)
def test_every_rules_skill_has_a_security_section(name: str):
    """Injection and secrets are language-specific; the general floor is not enough.

    Only php-rules used to carry a prepared-statements line. java, kotlin,
    csharp, go, rust and python rules had no injection rule at all, which made
    "follow the language rules" mean something different per language.
    """
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"^## Security", text, re.MULTILINE), (
        f"{name} has no `## Security` section"
    )


@pytest.mark.parametrize("name", RULES_SKILLS)
def test_rules_descriptions_name_the_files_they_apply_to(name: str):
    """The description is the trigger. An extension missing there never fires.

    `cpp-rules` shipped listing `.c/.h/.cpp/.cc/.hpp` while plugin.json mapped
    clangd over `.cxx`/`.hxx`/`.C`/`.H` too, so those files silently got no
    rules.
    """
    fm = _frontmatter(SKILLS_DIR / name / "SKILL.md")
    desc = fm["description"]
    assert "ONLY" in desc, f"{name}: description must scope itself with an ONLY clause"
    assert "." in desc and re.search(r"\.\w+", desc), (
        f"{name}: description names no file extension, so nothing tells the "
        "model when the skill applies"
    )


# ---------------------------------------------------------------------------
# The backend fleet stays one role, not twelve dialects
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", BACKEND_AGENTS, ids=lambda p: p.stem)
def test_backend_agents_carry_the_same_contract(path: Path):
    body = path.read_text(encoding="utf-8")
    lang = path.stem.removeprefix("backend-")

    assert f"{lang}-rules" in _skills_of(path), \
        f"{path.name} does not preload {lang}-rules"
    assert "ponytail" in _skills_of(path), f"{path.name} does not preload ponytail"

    for required in (
        "Validate external input at boundaries; parameterize every query.",
        "Verify before reporting done:",
        "Never touch frontend",
        "no dead code or broken imports left behind",
    ):
        assert required in body, (
            f"{path.name} is missing the shared backend contract line: {required!r}. "
            "All backend agents are one role; a line present in some and absent in "
            "others means the same request gets a different standard per language."
        )


def test_every_backend_agent_has_its_rules_skill_and_vice_versa():
    """A backend agent with no rules skill, or a language whose rules have no agent."""
    agent_langs = {p.stem.removeprefix("backend-") for p in BACKEND_AGENTS}
    rules_langs = {
        n.removesuffix("-rules") for n in RULES_SKILLS
    } - {"sql", "bash", "web-ui"}  # not languages an agent specializes in

    assert agent_langs == rules_langs, (
        "backend agents and language rules skills have drifted apart.\n"
        f"  agents without rules: {sorted(agent_langs - rules_langs)}\n"
        f"  rules without agents: {sorted(rules_langs - agent_langs)}"
    )


# ---------------------------------------------------------------------------
# Charters stay readable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_charters_stay_under_the_autoheal_limit(path: Path):
    n = len(path.read_text(encoding="utf-8").splitlines())
    assert n <= MAX_CHARTER_LINES, (
        f"{path.name} is {n} lines; agent-autoheal caps charters at "
        f"{MAX_CHARTER_LINES} and says the overflow belongs in a runbook or skill."
    )


# ---------------------------------------------------------------------------
# A constraint an agent cannot satisfy with its granted tools
# ---------------------------------------------------------------------------

def test_docs_writer_can_run_what_it_promises_to_verify():
    """Its charter promises executed examples; that needs Bash.

    This shipped as a contradiction: "keep examples runnable — copy-paste must
    work against the current code" in an agent granted Read/Write/Edit/Glob/Grep
    and nothing that could run anything.
    """
    path = AGENTS_DIR / "docs-writer.md"
    body = path.read_text(encoding="utf-8")
    if re.search(r"\brun every example\b|\bruns? them\b|\brunnable\b", body, re.I):
        assert "Bash" in _tools_of(path), (
            "docs-writer's charter promises verified/executed examples but the "
            "agent has no Bash tool, so the promise is unkeepable by construction."
        )


# ---------------------------------------------------------------------------
# Security findings need a shared vocabulary, not just good advice
# ---------------------------------------------------------------------------

_CWE = re.compile(r"\bCWE-\d{1,4}\b")


@pytest.mark.parametrize("name", RULES_SKILLS)
def test_security_sections_cite_cwe(name: str):
    """A finding without a CWE is a sentence; with one it is a class.

    Two reviewers writing "unsafe query building" and "user input reaches SQL"
    are reporting one defect, and only the ID makes that visible. It is also
    what lets a finding be deduped across a report and compared across
    languages — the reason these sections carry IDs rather than prose alone.
    """
    text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    section = text[text.index("## Security"):]

    ids = _CWE.findall(section)
    assert len(ids) >= 3, (
        f"{name}'s Security section cites {len(ids)} CWE ids; it should tag its "
        "footguns so a finding can be named the same way across languages."
    )


def test_security_baseline_ships_and_is_preloaded_where_it_matters():
    """The skill that says how to rank a finding has to reach the agents that rank."""
    assert (SKILLS_DIR / "security-baseline" / "SKILL.md").is_file()

    for agent in ("security-auditor", "reviewer"):
        assert "security-baseline" in _skills_of(AGENTS_DIR / f"{agent}.md"), (
            f"{agent} reports security findings but does not preload "
            "security-baseline, so it has no severity ladder and no CWE index — "
            "which is how an invented CVSS score gets into a report."
        )


def test_no_asset_tells_an_agent_to_derive_a_cvss_score():
    """A CVSS vector computed from a code read is fabricated precision.

    The deployment, network exposure, and data classification are all unknown to
    an agent reading a diff, and a made-up `7.5` carries more authority than the
    evidence behind it. Assets may *describe* CVSS or forbid deriving one; what
    none of them may do is instruct an agent to produce a score.

    Matches the instruction, not the word: a verb of production next to CVSS,
    minus any line that negates it. Prose about CVSS is fine and common — the
    security-baseline description and its own DON'T line both mention it.
    """
    produce = re.compile(
        r"\b(comput\w*|calculat\w*|assign\w*|deriv\w*|scor\w*|rate|rank)\b[^.]{0,60}\bCVSS\b"
        r"|\bCVSS\b[^.]{0,60}\b(comput\w*|calculat\w*|assign\w*|deriv\w*|yourself)\b",
        re.I,
    )
    negated = re.compile(r"\b(never|not|no|don't|do not|instead of|invent\w*|"
                         r"fabricat\w*|quote|without|cannot)\b", re.I)

    offenders = [
        f"{path.parent.name}/{path.name}: {line.strip()}"
        for path in [*AGENT_FILES, *SKILL_FILES]
        for line in path.read_text(encoding="utf-8").splitlines()
        if produce.search(line) and not negated.search(line)
    ]

    assert not offenders, (
        "assets instructing an agent to produce a CVSS score:\n  "
        + "\n  ".join(offenders)
    )


def test_security_audit_workflow_actually_gates_on_its_scanners():
    """`verify` claims the criticals are gone — something has to check.

    The node shipped with four commented-out `command_exit` lines, so it gated on
    nothing at all. They were commented out for a real reason (command_exit fails
    CLOSED on a missing binary, blocking every repo without that toolchain);
    quality_check is the fail-open equivalent, reading the command out of
    .vise/quality.yaml and skip-passing with an honest "not configured" record.
    """
    graph_path = (
        Path(__file__).resolve().parents[1]
        / "assets" / "workflows" / "security-audit-graph.yaml"
    )
    graph = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    verify = next(n for n in graph["nodes"] if n["id"] == "verify")
    checks = {
        v.get("check") for v in (verify.get("validators") or [])
        if v.get("type") == "quality_check"
    }

    assert {"sast", "sca", "secrets"} <= checks, (
        f"security-audit `verify` gates on {sorted(checks)}; it must re-run the "
        "sast, sca, and secrets checks it told the agent to run at `scan`."
    )


def test_scan_node_is_not_gated_on_scanner_exit_codes():
    """A scanner exiting nonzero at `scan` means it FOUND something.

    Node validators run on every traverse, not only on validators_green edges
    (`_graph_transition` runs the node gate before the edge condition is even
    examined). Gating `scan` on sast would therefore block the path to `triage`
    exactly when there is something to triage.
    """
    graph_path = (
        Path(__file__).resolve().parents[1]
        / "assets" / "workflows" / "security-audit-graph.yaml"
    )
    graph = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    scan = next(n for n in graph["nodes"] if n["id"] == "scan")

    assert not (scan.get("validators") or []), (
        "security-audit `scan` declares validators. The node gate runs them on "
        "every traverse, so a SAST that found a real finding would block the "
        "move to triage — the one phase that exists to process it."
    )


# ---------------------------------------------------------------------------
# A validator nobody can find is a validator nobody uses
# ---------------------------------------------------------------------------

def test_every_validator_in_the_registry_is_documented():
    """The same orphan failure as swift-rules, one layer down.

    A workflow author picks validators from the README table — the registry
    itself is not a document anyone reads. `files_exist` is used by zero bundled
    workflows and that is fine by design; being undiscoverable is not.
    """
    from vise.engines.validators import _REGISTRY

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    undocumented = sorted(
        name for name in _REGISTRY if f"`{name}`" not in readme
    )
    assert not undocumented, (
        f"validators in the registry but absent from README: {undocumented}. "
        "A workflow author has no way to discover them."
    )


# ---------------------------------------------------------------------------
# Los conteos de assets en CLAUDE.md derivan igual que derivaron los de hooks.
# ---------------------------------------------------------------------------
#
# "the 8 hook registrations" resultó no ser ningún conteo real — ni eventos, ni
# entradas, ni comandos, ni scripts. Había sido cierto alguna vez. Los conteos
# de skills, agentes y comandos están expuestos a lo mismo, y son lo primero
# que lee alguien que llega al repo.

def test_claude_md_counts_the_skills_that_actually_ship():
    import re

    shipped = len([p for p in (REPO / "skills").iterdir() if (p / "SKILL.md").exists()])
    claimed = re.search(r"(\d+) bundled skills", (REPO / "CLAUDE.md").read_text(encoding="utf-8"))
    assert claimed, "CLAUDE.md ya no dice cuántas skills hay"
    assert int(claimed.group(1)) == shipped, (
        f"CLAUDE.md dice {claimed.group(1)} skills, hay {shipped}"
    )


def test_claude_md_counts_the_agents_that_actually_ship():
    import re

    shipped = len(list((REPO / "agents").glob("*.md")))
    claimed = re.search(r"(\d+) bundled subagent charters", (REPO / "CLAUDE.md").read_text(encoding="utf-8"))
    assert claimed, "CLAUDE.md ya no dice cuántos agentes hay"
    assert int(claimed.group(1)) == shipped


def test_claude_md_lists_every_command():
    """Un comando que existe y no está listado es un comando que nadie encuentra."""
    claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    shipped = sorted(p.stem for p in (REPO / "commands").glob("*.md"))
    missing = [c for c in shipped if f"/{c}" not in claude_md]
    assert not missing, f"comandos que existen pero CLAUDE.md no nombra: {missing}"
