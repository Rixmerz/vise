"""Design gates fail closed; every other validator in this file skips.

``QualityCheckValidator`` (see ``test_gate_visibility.py``) SKIPS when its
tool is unbound or missing: ``passed=True, source="asserted",
outcome="unverified"``. That is correct for it — an unbound named check is
not a promise the project ever made.

``DesignTokensValidator``, ``UiLayoutValidator`` and ``UiContrastValidator``
take the opposite contract on purpose: every unavailable or misconfigured
path returns ``passed=False, source="mechanical"``, never
``outcome="unverified"``. A gate that could not run must not report success
— reporting a skip as a pass would let a project silently carry zero design
coverage while ``goal_complete`` reads it as "checked and clean". This file
pins that fail-closed behaviour for all three gates so any future change
that turns a red gate green by making it skip is caught here.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vise.engines import ui_checks
from vise.engines.design_profile import DEFAULT_BREAKPOINTS, load_design_config
from vise.engines.ui_contrast import check_nodes, contrast_ratio, required_ratio
from vise.engines.validators import _REGISTRY, build_validators


def _goal(project_dir: Path | str) -> SimpleNamespace:
    return SimpleNamespace(
        id="node:design", project_dir=str(project_dir), goal="test", validator_configs=[]
    )


def _assert_fails_closed(record) -> None:
    assert record.passed is False
    assert record.source == "mechanical"
    assert record.outcome != "unverified"


# ---------------------------------------------------------------------------
# Part 1 — the three validators via the registry
# ---------------------------------------------------------------------------


def test_registry_contains_the_three_design_gate_types() -> None:
    for type_name in ("design_tokens", "ui_layout", "ui_contrast"):
        assert type_name in _REGISTRY


def test_unknown_type_still_fails_closed(tmp_path: Path) -> None:
    validator = build_validators([{"type": "not_a_real_type"}])[0]

    record = validator.run(_goal(tmp_path))

    _assert_fails_closed(record)


def test_ui_layout_without_playwright_fails_closed_and_names_both_install_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates "no Playwright" via browser_status — this machine has it installed."""
    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status",
        lambda: (False, "playwright is not installed; run: pip install 'vise[design]'. "
                         "Full setup: pip install 'vise[design]' && playwright install chromium"),
    )
    validator = build_validators([{"type": "ui_layout"}])[0]

    record = validator.run(_goal(tmp_path))

    _assert_fails_closed(record)
    assert "pip install 'vise[design]'" in record.evidence
    assert "playwright install chromium" in record.evidence


def test_ui_contrast_without_playwright_fails_closed_and_names_both_install_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates "no Playwright" via browser_status — this machine has it installed."""
    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status",
        lambda: (False, "playwright is not installed; run: pip install 'vise[design]'. "
                         "Full setup: pip install 'vise[design]' && playwright install chromium"),
    )
    validator = build_validators([{"type": "ui_contrast"}])[0]

    record = validator.run(_goal(tmp_path))

    _assert_fails_closed(record)
    assert "pip install 'vise[design]'" in record.evidence
    assert "playwright install chromium" in record.evidence


def test_ui_layout_with_browser_but_no_targets_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vise.engines.render_harness.browser_status", lambda: (True, "ok"))
    validator = build_validators([{"type": "ui_layout"}])[0]

    record = validator.run(_goal(tmp_path))

    _assert_fails_closed(record)
    assert "targets" in record.evidence
    assert "empty" in record.evidence


def test_ui_contrast_with_browser_but_no_targets_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vise.engines.render_harness.browser_status", lambda: (True, "ok"))
    validator = build_validators([{"type": "ui_contrast"}])[0]

    record = validator.run(_goal(tmp_path))

    _assert_fails_closed(record)
    assert "targets" in record.evidence
    assert "empty" in record.evidence


def test_design_tokens_with_real_violation_fails_closed_with_finding_in_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISE_QUALITY_PROFILE", raising=False)
    (tmp_path / "app.css").write_text(".foo { color: #ff0000; }\n", encoding="utf-8")
    validator = build_validators([{"type": "design_tokens"}])[0]

    record = validator.run(_goal(tmp_path))

    _assert_fails_closed(record)
    assert "raw_color" in record.evidence


def test_design_tokens_with_no_ui_files_passes_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISE_QUALITY_PROFILE", raising=False)
    (tmp_path / "README.md").write_text("no UI here\n", encoding="utf-8")
    validator = build_validators([{"type": "design_tokens"}])[0]

    record = validator.run(_goal(tmp_path))

    assert record.passed is True
    assert record.source == "mechanical"
    assert record.outcome == "verified"


def test_design_tokens_with_generous_allowances_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.css").write_text(".foo { color: #ff0000; }\n", encoding="utf-8")
    profile = tmp_path / "quality.yaml"
    profile.write_text(
        "design:\n  allowances:\n    raw_color: 100\n    no_font_family: 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))
    validator = build_validators([{"type": "design_tokens"}])[0]

    record = validator.run(_goal(tmp_path))

    assert record.passed is True
    assert record.source == "mechanical"
    assert record.outcome == "verified"


# ---------------------------------------------------------------------------
# Part 2 — design_profile.load_design_config never raises
# ---------------------------------------------------------------------------


def test_load_design_config_missing_file_returns_empty(tmp_path: Path) -> None:
    config = load_design_config(tmp_path)

    assert config.targets == ()
    assert config.breakpoints == DEFAULT_BREAKPOINTS
    assert config.allowances == {}


def test_load_design_config_document_is_a_list_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "quality.yaml"
    profile.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    config = load_design_config(tmp_path)

    assert config.targets == ()


def test_load_design_config_design_key_is_a_string_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "quality.yaml"
    profile.write_text("design: not-a-mapping\n", encoding="utf-8")
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    config = load_design_config(tmp_path)

    assert config.targets == ()


def test_load_design_config_targets_as_bare_string_becomes_one_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "quality.yaml"
    profile.write_text("design:\n  targets: http://localhost:3000/\n", encoding="utf-8")
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    config = load_design_config(tmp_path)

    assert config.targets == ("http://localhost:3000/",)


def test_load_design_config_negative_and_nonnumeric_allowances_are_clamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "quality.yaml"
    profile.write_text(
        "design:\n  allowances:\n    raw_color: -5\n    raw_spacing: not-a-number\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    config = load_design_config(tmp_path)

    assert config.allowance("raw_color") == 0
    assert "raw_spacing" not in config.allowances


def test_load_design_config_empty_breakpoints_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "quality.yaml"
    profile.write_text("design:\n  breakpoints: []\n", encoding="utf-8")
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    config = load_design_config(tmp_path)

    assert config.breakpoints == DEFAULT_BREAKPOINTS


# ---------------------------------------------------------------------------
# Part 3 — ui_checks geometry, no browser
# ---------------------------------------------------------------------------


def test_check_snapshot_oversized_child_in_clipping_parent_is_internal_clip() -> None:
    snapshot = {
        "nodes": [
            {
                "id": "parent",
                "rect": {"x": 0, "y": 0, "width": 50, "height": 50},
                "styles": {"overflow": "hidden"},
                "children": ["child"],
            },
            {"id": "child", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}},
        ]
    }

    defects = ui_checks.check_snapshot(snapshot)

    clips = [d for d in defects if d.kind == "internal_clip"]
    assert len(clips) == 1
    assert clips[0].a == "child"
    assert clips[0].b == "parent"


def test_check_snapshot_overlapping_unrelated_boxes_is_external_collision_with_exact_delta() -> None:
    snapshot = {
        "nodes": [
            {"id": "a", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}},
            {"id": "b", "rect": {"x": 60, "y": 0, "width": 100, "height": 100}},
        ]
    }

    defects = ui_checks.check_snapshot(snapshot)

    collisions = [d for d in defects if d.kind == "external_collision"]
    assert len(collisions) == 1
    assert collisions[0].delta_px == 40.0


def test_check_snapshot_node_outside_document_bounds_is_offpage() -> None:
    snapshot = {
        "nodes": [
            {"id": "a", "rect": {"x": 90, "y": 0, "width": 50, "height": 20}},
        ],
        "document": {"scrollWidth": 100, "scrollHeight": 100},
    }

    defects = ui_checks.check_snapshot(snapshot)

    offpage = [d for d in defects if d.kind == "offpage"]
    assert len(offpage) == 1
    assert offpage[0].a == "a"


def test_check_snapshot_unresolved_selectors_produce_one_defect_each() -> None:
    snapshot = {
        "nodes": [],
        "unresolved": ["missing-1", "missing-2"],
    }

    defects = ui_checks.check_snapshot(snapshot)

    unresolved = [d for d in defects if d.kind == "unresolved_selector"]
    assert {d.a for d in unresolved} == {"missing-1", "missing-2"}
    assert len(unresolved) == 2


def test_check_snapshot_clean_layout_produces_no_defects() -> None:
    snapshot = {
        "nodes": [
            {
                "id": "parent",
                "rect": {"x": 0, "y": 0, "width": 200, "height": 200},
                "styles": {"overflow": "visible"},
                "children": ["child"],
            },
            {"id": "child", "rect": {"x": 10, "y": 10, "width": 50, "height": 50}},
        ],
        "document": {"scrollWidth": 200, "scrollHeight": 200},
    }

    defects = ui_checks.check_snapshot(snapshot)

    assert defects == []


def test_check_snapshot_malformed_nodes_do_not_raise() -> None:
    snapshot = {
        "nodes": [
            {"id": "no_rect"},
            {"id": "none_rect", "rect": None},
            {"id": "bad_dims", "rect": {"x": 0, "y": 0, "width": None, "height": 10}},
            "not-even-a-dict",
            {"missing": "id"},
        ],
        "unresolved": "not-a-list",
        "document": "not-a-dict",
    }

    defects = ui_checks.check_snapshot(snapshot)

    assert isinstance(defects, list)


def test_check_breakpoints_same_defect_at_three_widths_collapses_to_largest_delta() -> None:
    def _snapshot(offset: float) -> dict:
        return {
            "nodes": [
                {"id": "a", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}},
                {"id": "b", "rect": {"x": offset, "y": 0, "width": 100, "height": 100}},
            ]
        }

    snapshots = {375: _snapshot(90), 768: _snapshot(80), 1280: _snapshot(70)}

    defects = ui_checks.check_breakpoints(snapshots)

    collisions = [d for d in defects if d.kind == "external_collision"]
    assert len(collisions) == 1
    assert collisions[0].delta_px == 30.0
    assert collisions[0].breakpoint == 1280
    assert "3 breakpoints" in collisions[0].detail


# ---------------------------------------------------------------------------
# Part 4 — ui_contrast maths, no browser
# ---------------------------------------------------------------------------


def test_contrast_ratio_black_on_white_is_21() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)


def test_contrast_ratio_white_on_white_is_1() -> None:
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)


def test_contrast_ratio_777777_on_white_is_just_under_aa() -> None:
    assert contrast_ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_contrast_ratio_767676_on_white_is_just_over_aa() -> None:
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


def test_contrast_ratio_composites_alpha_instead_of_ignoring_it() -> None:
    opaque = contrast_ratio("#000000", "#ffffff")
    translucent = contrast_ratio("rgba(0,0,0,0.5)", "#ffffff")

    assert translucent != pytest.approx(opaque)


def test_required_ratio_24px_is_3() -> None:
    assert required_ratio(24.0, "normal") == 3.0


def test_required_ratio_1866px_bold_is_3() -> None:
    assert required_ratio(18.66, "bold") == 3.0


def test_required_ratio_1866px_normal_is_45() -> None:
    assert required_ratio(18.66, "normal") == 4.5


def test_required_ratio_unknown_font_size_fails_safe_to_45() -> None:
    assert required_ratio(None, "normal") == 4.5


def test_check_nodes_ratio_34_finds_at_14px_and_clears_at_28px() -> None:
    def _node(font_size: str) -> dict:
        return {
            "id": "label",
            "rect": {"x": 0, "y": 0, "width": 40, "height": 20},
            "styles": {
                "color": "#8a8a8a",
                "effective_background": "#ffffff",
                "font_size": font_size,
                "font_weight": "normal",
            },
        }

    small_findings = check_nodes([_node("14px")])
    large_findings = check_nodes([_node("28px")])

    assert len(small_findings) == 1
    assert large_findings == []


def test_check_nodes_garbage_input_returns_empty_without_raising() -> None:
    nodes = [
        {"id": "unparseable", "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
         "styles": {"color": "not-a-color", "effective_background": "#ffffff"}},
        {"id": "missing-styles", "rect": {"x": 0, "y": 0, "width": 10, "height": 10}},
        {},
        "not-even-a-dict",
    ]

    findings = check_nodes(nodes)

    assert findings == []


# --------------------------------------------------------------------------
# Security regressions. A gate reads whatever the repo hands it, so the repo
# boundary is a trust boundary — both of these were reproduced before the fix.
# --------------------------------------------------------------------------


def test_a_symlink_out_of_the_project_is_not_read(tmp_path: Path) -> None:
    """CWE-59. `Path.is_file()` follows links, so a committed `theme.css`
    pointing at a file outside the tree was read and its matched fragments
    reached the persisted evidence string."""
    from vise.engines.design_tokens import scan

    outside = tmp_path / "outside.css"
    outside.write_text(".s { color: #a1b2c3 }", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "real.css").write_text("body { color: #00ff00 }", encoding="utf-8")
    (project / "theme2.css").symlink_to(outside)

    report = scan(project)
    details = " ".join(f.detail for f in report.findings)

    assert "#00ff00" in details, "the real file inside the project is still scanned"
    assert "#a1b2c3" not in details, "content from outside the project must never be read"


def test_a_target_that_is_not_a_url_is_refused_not_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CWE-918. The harness treats a non-URL target as inline HTML and calls
    `set_content` on it, which would execute repo-supplied markup and script in
    the browser on whatever machine runs the gate."""
    import vise.engines.render_harness as render_harness
    from vise.engines.validators import build_validators

    monkeypatch.setattr(render_harness, "browser_status", lambda: (True, "ok"))
    profile = tmp_path / "quality.yaml"
    profile.write_text(
        'design:\n  targets: ["<script>fetch(1)</script>", "javascript:alert(1)"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))
    goal = SimpleNamespace(
        id="g", project_dir=str(tmp_path), goal="x", validator_configs=[]
    )

    for validator_type in ("ui_layout", "ui_contrast"):
        record = build_validators([{"type": validator_type}])[0].run(goal)

        assert record.passed is False, f"{validator_type} rendered a non-URL target"
        assert record.source == "mechanical"
        assert "not loadable" in record.evidence


def test_rejected_targets_are_reported_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping them quietly would leave a gate reporting clean on a
    configuration it never honoured."""
    from vise.engines.design_profile import load_design_config

    profile = tmp_path / "quality.yaml"
    profile.write_text(
        'design:\n  targets: ["https://ok.example/", "not-a-url"]\n', encoding="utf-8"
    )
    monkeypatch.setenv("VISE_QUALITY_PROFILE", str(profile))

    config = load_design_config(tmp_path)

    assert config.targets == ("https://ok.example/",)
    assert config.rejected_targets == ("not-a-url",)
