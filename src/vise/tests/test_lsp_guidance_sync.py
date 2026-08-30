"""Every rules skill whose language has a server says when to reach for it.

Declaring a language server, and even handing every agent the `LSP` tool, does
not make anything use it. An agent about to change code reaches for `Grep`,
because grep is the habit and it always returns something. The guidance has to
arrive at the moment of that decision — which is when a `*-rules` skill loads,
because that is keyed to the file extension under edit.

The README claimed this was already true ("each `*-rules` skill states the
circumstance that requires a lookup"). It was not: zero of the fifteen mentioned
`LSP`. These tests are what makes the claim checkable rather than aspirational.

The other half matters as much. `bash-rules`, `sql-rules` and `web-ui-rules`
cover extensions no declared server handles, and telling an agent to use a
language server on a `.sh` file is not a harmless extra — it is a lookup that
returns nothing, on advice vise gave.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

#: The complete operation set of Claude Code's `LSP` tool, read out of its own
#: schema. An asset naming anything else is telling an agent to call something
#: that does not exist.
LSP_OPERATIONS = frozenset({
    "goToDefinition", "findReferences", "hover", "documentSymbol",
    "workspaceSymbol", "goToImplementation", "prepareCallHierarchy",
    "incomingCalls", "outgoingCalls",
})

NAVIGATION_HEADING = "## Navigation — the language server, not grep"

#: Rules skills whose extensions no declared server covers.
WITHOUT_A_SERVER = frozenset({"bash-rules", "sql-rules", "web-ui-rules"})


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            return parent
    pytest.skip("not running from a plugin checkout")


@pytest.fixture(scope="module")
def root() -> Path:
    return _root()


@pytest.fixture(scope="module")
def served_extensions(root: Path) -> frozenset[str]:
    manifest = json.loads(
        (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    exts: set[str] = set()
    for cfg in manifest.get("lspServers", {}).values():
        exts.update(e.lower() for e in cfg.get("extensionToLanguage", {}))
    return frozenset(exts)


def _rules_skills(root: Path) -> list[Path]:
    return sorted((root / "skills").glob("*-rules/SKILL.md"))


def test_the_split_is_what_the_manifest_says_it_is(root: Path, served_extensions):
    """The exemption list is a claim about plugin.json; check it against it.

    If a server is ever added for shell or SQL, this fails and the skill gets
    its section — rather than the list quietly staying right for the wrong
    reason.
    """
    names = {p.parent.name for p in _rules_skills(root)}
    assert WITHOUT_A_SERVER <= names
    for ext in (".sh", ".bash", ".zsh", ".sql", ".html", ".css", ".scss"):
        assert ext not in served_extensions, (
            f"{ext} now has a declared server — the rules skill covering it "
            f"should gain a navigation section and leave WITHOUT_A_SERVER"
        )


def test_every_served_language_says_when_to_use_the_server(root: Path):
    missing = [
        p.parent.name for p in _rules_skills(root)
        if p.parent.name not in WITHOUT_A_SERVER
        and NAVIGATION_HEADING not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"these languages have a declared server and no guidance on when to "
        f"prefer it over grep: {missing}"
    )


def test_a_language_with_no_server_is_not_told_to_use_one(root: Path):
    """Advice that cannot work is worse than none: it spends a call and
    teaches the agent the tool is useless."""
    offenders = [
        p.parent.name for p in _rules_skills(root)
        if p.parent.name in WITHOUT_A_SERVER
        and NAVIGATION_HEADING in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"no declared server covers these extensions: {offenders}"


def test_the_guidance_names_the_alternative_it_beats(root: Path):
    """"Use the language server" loses to habit. Naming grep is the point."""
    for path in _rules_skills(root):
        if path.parent.name in WITHOUT_A_SERVER:
            continue
        section = path.read_text(encoding="utf-8").split(NAVIGATION_HEADING, 1)[1]
        section = section.split("\n## ", 1)[0]
        assert "grep" in section.lower(), f"{path.parent.name} never mentions grep"


def test_every_lsp_operation_named_in_an_asset_exists(root: Path):
    """Same rule as `test_asset_honesty`: no asset names a call vise cannot make."""
    pattern = re.compile(r"`(goTo[A-Za-z]+|find[A-Za-z]+|[a-z][a-zA-Z]*Calls|"
                         r"documentSymbol|workspaceSymbol|hover)`")
    bad: dict[str, set[str]] = {}
    for path in list((root / "skills").rglob("SKILL.md")) + \
            list((root / "agents").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for name in pattern.findall(text):
            # Only judge names that look like an LSP operation attempt.
            if name.startswith(("goTo", "find")) or name.endswith("Calls") \
                    or name in {"documentSymbol", "workspaceSymbol", "hover"}:
                if name not in LSP_OPERATIONS:
                    bad.setdefault(str(path.relative_to(root)), set()).add(name)
    assert not bad, f"assets name LSP operations that do not exist: {bad}"


def test_the_baseline_carries_the_general_rule(root: Path):
    """Every code-touching agent loads engineering-baseline, so the
    language-agnostic version of the decision belongs there once."""
    text = (root / "skills" / "engineering-baseline" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "language server before grep" in text
    for operation in ("findReferences", "goToDefinition", "goToImplementation",
                      "documentSymbol"):
        assert operation in text, f"baseline never names {operation}"


def test_the_baseline_states_the_limits(root: Path):
    """A rule with no stated limit gets applied where it does not hold, and
    then distrusted everywhere."""
    text = (root / "skills" / "engineering-baseline" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Dynamic dispatch is invisible" in text
    assert "no server" in text.lower()
