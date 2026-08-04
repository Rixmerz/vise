"""Tests for openspec_profile and validators.OpenSpecValidator.

Coverage: the structural reader (root detection, VISE_OPENSPEC_ROOT override,
archive exclusion, delta/scenario/task parsing, fenced-code immunity) and every
``require`` level of the validator — including the two properties that make the
gate trustworthy: levels 1-4 FAIL CLOSED without touching the network or PATH,
and level 5 degrades to ``source="asserted"`` rather than inventing a verdict.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vise.engines.openspec_profile import (
    OPENSPEC_ROOT_ENV,
    active_changes,
    openspec_root,
)
from vise.engines.validators import OpenSpecValidator, build_validators


def _goal(project_dir) -> SimpleNamespace:
    return SimpleNamespace(id="node:spec", project_dir=str(project_dir), goal="test")


WELL_FORMED_DELTA = """\
## ADDED Requirements

### Requirement: Users can log in
The system SHALL authenticate users by email and password.

#### Scenario: Valid credentials
- **WHEN** a user submits a correct email and password
- **THEN** a session token is returned
"""


def _make_change(
    tmp_path: Path,
    name: str = "add-auth",
    *,
    proposal: bool = True,
    delta: str | None = WELL_FORMED_DELTA,
    tasks: str | None = "- [ ] Build it\n",
    archived: bool = False,
) -> Path:
    parent = tmp_path / "openspec" / "changes"
    if archived:
        parent = parent / "archive"
    change = parent / name
    change.mkdir(parents=True, exist_ok=True)
    (change / ".openspec.yaml").write_text("schema: spec-driven\n", encoding="utf-8")
    if proposal:
        (change / "proposal.md").write_text("## Why\nBecause.\n", encoding="utf-8")
    if delta is not None:
        spec = change / "specs" / "auth" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(delta, encoding="utf-8")
    if tasks is not None:
        (change / "tasks.md").write_text(tasks, encoding="utf-8")
    return change


@pytest.fixture(autouse=True)
def _no_root_override(monkeypatch):
    """Every test states its own root; a stray env var must not leak in."""
    monkeypatch.delenv(OPENSPEC_ROOT_ENV, raising=False)


# ---------------------------------------------------------------------------
# openspec_profile — the structural reader
# ---------------------------------------------------------------------------


def test_no_root_returns_none(tmp_path):
    assert openspec_root(tmp_path) is None
    assert active_changes(tmp_path) == []


def test_root_detected(tmp_path):
    (tmp_path / "openspec").mkdir()
    assert openspec_root(tmp_path) == tmp_path / "openspec"


def test_root_env_override(tmp_path, monkeypatch):
    elsewhere = tmp_path / "planning"
    elsewhere.mkdir()
    monkeypatch.setenv(OPENSPEC_ROOT_ENV, str(elsewhere))
    # project_dir has no openspec/ of its own — the override is what resolves.
    assert openspec_root(tmp_path / "code") == elsewhere


def test_root_without_changes_dir_yields_no_changes(tmp_path):
    (tmp_path / "openspec").mkdir()
    assert active_changes(tmp_path) == []


def test_change_is_parsed(tmp_path):
    _make_change(tmp_path)
    (c,) = active_changes(tmp_path)
    assert c.name == "add-auth"
    assert c.has_proposal
    assert c.has_tasks
    assert (c.tasks_total, c.tasks_done) == (1, 0)
    assert c.deltas.well_formed
    assert c.deltas.requirements == 1
    assert c.deltas.headers == 1
    assert c.deltas.orphan_requirements == ()


def test_archived_changes_are_excluded(tmp_path):
    _make_change(tmp_path, "landed", archived=True)
    assert active_changes(tmp_path) == []


def test_requirement_without_scenario_is_an_orphan(tmp_path):
    _make_change(tmp_path, delta=(
        "## ADDED Requirements\n\n"
        "### Requirement: Users can log in\nThe system SHALL do it.\n"
    ))
    (c,) = active_changes(tmp_path)
    assert not c.deltas.well_formed
    assert c.deltas.orphan_requirements == ("Users can log in",)


def test_delta_without_header_is_not_well_formed(tmp_path):
    _make_change(tmp_path, delta=(
        "### Requirement: Users can log in\n\n#### Scenario: ok\n- **WHEN** x\n"
    ))
    (c,) = active_changes(tmp_path)
    assert not c.deltas.well_formed
    assert c.deltas.headers == 0


def test_fenced_code_is_not_counted_as_a_delta(tmp_path):
    """A proposal that *documents* the syntax must not read as declaring it.

    Without fence stripping, any template or how-to that shows a delta block
    would report a well-formed change the CLI then rejects — a false green
    produced by prose.
    """
    _make_change(tmp_path, delta=(
        "Here is how you write one:\n\n"
        "```markdown\n"
        "## ADDED Requirements\n"
        "### Requirement: Example\n"
        "#### Scenario: Example\n"
        "```\n"
    ))
    (c,) = active_changes(tmp_path)
    assert c.deltas.headers == 0
    assert c.deltas.requirements == 0
    assert not c.deltas.well_formed


def test_task_counting(tmp_path):
    _make_change(tmp_path, tasks="- [x] One\n- [X] Two\n- [ ] Three\n")
    (c,) = active_changes(tmp_path)
    assert (c.tasks_total, c.tasks_done) == (3, 2)
    assert not c.tasks_complete


def test_all_tasks_ticked_is_complete(tmp_path):
    _make_change(tmp_path, tasks="- [x] One\n- [x] Two\n")
    (c,) = active_changes(tmp_path)
    assert c.tasks_complete


def test_empty_tasks_file_is_not_complete(tmp_path):
    """Zero-of-zero is the scaffolded-but-unwritten shape, not done."""
    _make_change(tmp_path, tasks="# Tasks\n\nTBD\n")
    (c,) = active_changes(tmp_path)
    assert c.tasks_total == 0
    assert not c.tasks_complete


def test_unreadable_tree_degrades_to_empty(tmp_path):
    """A file where changes/ should be must not raise."""
    (tmp_path / "openspec").mkdir()
    (tmp_path / "openspec" / "changes").write_text("not a dir", encoding="utf-8")
    assert active_changes(tmp_path) == []


# ---------------------------------------------------------------------------
# OpenSpecValidator — require levels
# ---------------------------------------------------------------------------


def test_missing_root_fails_closed_at_every_level(tmp_path):
    for level in ("structure", "change", "deltas", "tasks_complete"):
        rec = OpenSpecValidator(require=level).run(_goal(tmp_path))
        assert rec.passed is False, f"{level} must fail closed with no openspec/"
        assert "openspec init" in rec.evidence
        assert rec.source == "mechanical"


def test_structure_passes_with_bare_root(tmp_path):
    (tmp_path / "openspec").mkdir()
    rec = OpenSpecValidator(require="structure").run(_goal(tmp_path))
    assert rec.passed is True
    assert rec.source == "mechanical"


def test_change_level_requires_an_active_change(tmp_path):
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    rec = OpenSpecValidator(require="change").run(_goal(tmp_path))
    assert rec.passed is False
    assert "no active change" in rec.evidence


def test_change_level_requires_a_proposal(tmp_path):
    _make_change(tmp_path, proposal=False)
    rec = OpenSpecValidator(require="change").run(_goal(tmp_path))
    assert rec.passed is False
    assert "proposal.md" in rec.evidence


def test_change_level_passes(tmp_path):
    _make_change(tmp_path)
    rec = OpenSpecValidator(require="change").run(_goal(tmp_path))
    assert rec.passed is True
    assert "add-auth" in rec.evidence


def test_deltas_level_names_the_orphan_requirement(tmp_path):
    _make_change(tmp_path, delta="## ADDED Requirements\n\n### Requirement: Solo\n")
    rec = OpenSpecValidator(require="deltas").run(_goal(tmp_path))
    assert rec.passed is False
    assert "Scenario" in rec.evidence
    assert "Solo" in rec.evidence


def test_deltas_level_names_missing_files(tmp_path):
    _make_change(tmp_path, delta=None)
    rec = OpenSpecValidator(require="deltas").run(_goal(tmp_path))
    assert rec.passed is False
    assert "no specs/**/*.md" in rec.evidence


def test_deltas_level_passes(tmp_path):
    _make_change(tmp_path)
    rec = OpenSpecValidator(require="deltas").run(_goal(tmp_path))
    assert rec.passed is True


def test_tasks_complete_reports_progress_on_failure(tmp_path):
    _make_change(tmp_path, tasks="- [x] One\n- [ ] Two\n")
    rec = OpenSpecValidator(require="tasks_complete").run(_goal(tmp_path))
    assert rec.passed is False
    assert "1/2 tasks" in rec.evidence


def test_tasks_complete_passes(tmp_path):
    _make_change(tmp_path, tasks="- [x] One\n")
    rec = OpenSpecValidator(require="tasks_complete").run(_goal(tmp_path))
    assert rec.passed is True


def test_change_field_pins_a_specific_change(tmp_path):
    _make_change(tmp_path, "add-auth", tasks="- [x] done\n")
    _make_change(tmp_path, "other-work", tasks="- [ ] pending\n")
    assert OpenSpecValidator(require="tasks_complete", change="add-auth").run(
        _goal(tmp_path)).passed is True
    assert OpenSpecValidator(require="tasks_complete", change="other-work").run(
        _goal(tmp_path)).passed is False


def test_unknown_pinned_change_fails_closed(tmp_path):
    _make_change(tmp_path)
    rec = OpenSpecValidator(require="tasks_complete", change="nope").run(_goal(tmp_path))
    assert rec.passed is False
    assert "not found" in rec.evidence


def test_unknown_require_fails_closed(tmp_path):
    _make_change(tmp_path)
    rec = OpenSpecValidator(require="bogus").run(_goal(tmp_path))
    assert rec.passed is False
    assert "unknown require" in rec.evidence


def test_empty_require_fails_closed(tmp_path):
    _make_change(tmp_path)
    rec = OpenSpecValidator(require="").run(_goal(tmp_path))
    assert rec.passed is False


# ---------------------------------------------------------------------------
# validated — the one tier that shells out, and its degradation
# ---------------------------------------------------------------------------


def test_validated_skips_as_asserted_when_cli_absent(tmp_path, monkeypatch):
    """No CLI must never be graded as verified — the whole reason levels 1-4
    are pure Python is so this degradation costs depth, not coverage.
    """
    (tmp_path / "openspec").mkdir()
    monkeypatch.setattr("vise.engines.validators._runnable", lambda exe, pd: False)
    rec = OpenSpecValidator(require="validated").run(_goal(tmp_path))
    assert rec.passed is True
    assert rec.source == "asserted", "a skip must not count as mechanical evidence"
    assert "npm i -g" in rec.evidence


def test_validated_treats_an_empty_set_as_asserted(tmp_path, monkeypatch):
    """`openspec validate` exits 0 over zero items. Passing that as mechanical
    would let a repo with an empty openspec/ report a verified spec gate.
    """
    (tmp_path / "openspec").mkdir()
    monkeypatch.setattr("vise.engines.validators._runnable", lambda exe, pd: True)
    monkeypatch.setattr(
        "vise.engines.validators.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout='{"summary":{"totals":{"items":0,"failed":0}}}', stderr=""),
    )
    rec = OpenSpecValidator(require="validated").run(_goal(tmp_path))
    assert rec.passed is True
    assert rec.source == "asserted"
    assert "nothing to validate" in rec.evidence


def test_validated_fails_and_quotes_the_cli_error(tmp_path, monkeypatch):
    (tmp_path / "openspec").mkdir()
    payload = (
        '{"items":[{"id":"add-auth","valid":false,"issues":'
        '[{"level":"ERROR","message":"Change must have at least one delta."}]}],'
        '"summary":{"totals":{"items":1,"failed":1}}}'
    )
    monkeypatch.setattr("vise.engines.validators._runnable", lambda exe, pd: True)
    monkeypatch.setattr(
        "vise.engines.validators.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=payload, stderr=""),
    )
    rec = OpenSpecValidator(require="validated").run(_goal(tmp_path))
    assert rec.passed is False
    assert rec.source == "mechanical"
    assert "at least one delta" in rec.evidence
    assert "add-auth" in rec.evidence


def test_validated_passes_on_a_clean_run(tmp_path, monkeypatch):
    (tmp_path / "openspec").mkdir()
    monkeypatch.setattr("vise.engines.validators._runnable", lambda exe, pd: True)
    monkeypatch.setattr(
        "vise.engines.validators.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout='{"summary":{"totals":{"items":3,"failed":0}}}', stderr=""),
    )
    rec = OpenSpecValidator(require="validated").run(_goal(tmp_path))
    assert rec.passed is True
    assert rec.source == "mechanical"
    assert "3/3 valid" in rec.evidence


def test_validated_falls_back_to_exit_code_on_unparseable_output(tmp_path, monkeypatch):
    (tmp_path / "openspec").mkdir()
    monkeypatch.setattr("vise.engines.validators._runnable", lambda exe, pd: True)
    monkeypatch.setattr(
        "vise.engines.validators.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="not json", stderr=""),
    )
    rec = OpenSpecValidator(require="validated").run(_goal(tmp_path))
    assert rec.passed is False


# ---------------------------------------------------------------------------
# registry wiring
# ---------------------------------------------------------------------------


def test_build_validators_recognises_openspec() -> None:
    vs = build_validators([{"type": "openspec", "require": "deltas", "weight": 0.5}])
    assert len(vs) == 1
    assert isinstance(vs[0], OpenSpecValidator)
    assert vs[0].require == "deltas"
    assert vs[0].weight == pytest.approx(0.5)


def test_openspec_is_not_an_unknown_validator() -> None:
    """Regression: a bare `- type: openspec` in a graph must build the real
    validator, not the fail-closed stand-in for a typo.
    """
    from vise.engines.validators import UnknownValidator
    (v,) = build_validators([{"type": "openspec"}])
    assert not isinstance(v, UnknownValidator)
    assert v.require == "change", "default level must still assert something real"
