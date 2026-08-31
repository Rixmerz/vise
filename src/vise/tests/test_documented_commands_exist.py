"""Every `vise ...` command this repo tells you to run, run against the parser.

Two places said `vise explain <run>`. There is no `explain` subcommand — it is
`vise runtime explain` — so following either instruction produced a usage error.
Nothing noticed, because a command line in a comment is prose, and prose is not
executed.

It is executed here. The commands are extracted from the files rather than
listed, so a new one that does not exist fails on the day it is written, and a
subcommand that gets renamed fails everywhere it is mentioned rather than in
whichever file someone remembered.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]

SEARCH = [
    REPO / "README.md",
    REPO / "CLAUDE.md",
    REPO / "docs",
    REPO / "src" / "vise",
    REPO / "skills",
    REPO / "agents",
    REPO / "commands",
]

#: A command line, not a sentence. Only inside inline code (`vise ...`) or a
#: fence explicitly marked as shell — anywhere else, "vise" is the subject of a
#: sentence and the next word is a verb. A denylist of English words was the
#: first attempt and it is unfinishable; this is syntactic, so it stays right
#: as the prose changes.
# `[ \t]*`, not `\s*`: a backtick closing the previous line followed by a
# newline would otherwise let a sentence beginning with "vise" read as a
# command line.
_INLINE = re.compile(
    r"`[ \t]*(?:\$[ \t]*)?vise[ \t]+([a-z][a-z0-9-]*)(?:[ \t]+([a-z][a-z0-9-]*))?[^`\n]*`"
)
_PROMPT = re.compile(r"^\s*(?:\$\s*)?vise\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?",
                     re.MULTILINE)

#: Handled by `main` before the parser is built, so they are real commands the
#: subparser tree does not know about.
_PRE_PARSER = {"version", "help", "doctor", "migrate-state"}


def _files():
    for root in SEARCH:
        if root.is_file():
            yield root
        elif root.is_dir():
            for suffix in ("*.md", "*.py", "*.yaml", "*.sh"):
                yield from root.rglob(suffix)


def _parser_tree() -> dict[str, set[str]]:
    """{subcommand: {its subcommands}} straight out of the real parser."""
    from vise.cli import bootstrap_cmd, experience_cmd, graph_cmd
    from vise.cli import insights_cmd, runtime_cmd, shot_cmd

    # Built the same way `main` builds it. Reaching into the modules rather
    # than skipping when no builder is exported: a doc-sync test that opts out
    # when it cannot see its subject is the exact shape this tier is against.
    parser = argparse.ArgumentParser(prog="vise")
    sub = parser.add_subparsers(dest="command")
    for module in (graph_cmd, experience_cmd, insights_cmd, bootstrap_cmd,
                   runtime_cmd, shot_cmd):
        module.add_parser(sub)

    tree: dict[str, set[str]] = {name: set() for name in _PRE_PARSER}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            nested: set[str] = set()
            for sub_action in sub._actions:
                if isinstance(sub_action, argparse._SubParsersAction):
                    nested |= set(sub_action.choices)
            tree[name] = nested
    return tree


def _documented() -> dict[tuple[str, str | None], list[str]]:
    """{(command, subcommand): [files that say so]}"""
    found: dict[tuple[str, str | None], list[str]] = {}
    tree = _parser_tree()
    for path in _files():
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = list(_INLINE.findall(text))
        for block in re.findall(r"```(?:bash|sh|shell|console)\n(.*?)```", text, re.S):
            matches += _PROMPT.findall(block)
        for cmd, sub in matches:
            # Only treat the second word as a subcommand when the first one
            # actually has subcommands; otherwise it is a flag or an argument.
            nested = sub if (cmd in tree and tree[cmd] and sub) else None
            found.setdefault((cmd, nested or None), []).append(
                str(path.relative_to(REPO))
            )
    return found


def test_the_extractor_finds_something():
    """A regex that matches nothing would make this file pass forever."""
    assert _documented(), "no `vise ...` commands found — the pattern has drifted"


def test_every_documented_command_exists():
    tree = _parser_tree()
    missing = {
        f"vise {cmd}": sorted(set(files))
        for (cmd, _sub), files in _documented().items()
        if cmd not in tree
    }
    assert not missing, (
        f"these are written as commands and the CLI has no such subcommand: "
        f"{missing}"
    )


def test_every_documented_subcommand_exists():
    tree = _parser_tree()
    missing = {
        f"vise {cmd} {sub}": sorted(set(files))
        for (cmd, sub), files in _documented().items()
        if sub is not None and cmd in tree and sub not in tree[cmd]
    }
    assert not missing, (
        f"these name a subcommand that does not exist: {missing}. "
        f"`vise explain <run>` was written in four places; it is "
        f"`vise runtime explain`"
    )


def test_session_id_is_not_documented_as_isolation():
    """`session_id` keys a project_dir cache. It isolates nothing.

    Thirteen tool docstrings called it "parallel session isolation", which
    describes a property vise does not have: two sessions on one project read
    and write the same graph state file. A caller who believed the docstring
    would run two workflows against one repo and expect them not to collide.
    """
    from vise.core.session import resolve_project_dir

    hits = []
    for path in (REPO / "src" / "vise").rglob("*.py"):
        if path.name == Path(__file__).name:
            continue  # this file names the claim in order to forbid it
        if "session isolation" in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(REPO)))
    assert not hits, f"these still promise session isolation: {sorted(hits)}"

    # And the behaviour the corrected wording describes.
    first, sid = resolve_project_dir("/tmp/one", "session-a")
    again, _ = resolve_project_dir(None, "session-a")
    assert (first, again) == ("/tmp/one", "/tmp/one"), "it must remember"
    assert sid == "session-a"

    other, _ = resolve_project_dir("/tmp/one", "session-b")
    assert other == "/tmp/one", (
        "a second session pointed at the same project gets the same project — "
        "which is the point: nothing is isolated"
    )
