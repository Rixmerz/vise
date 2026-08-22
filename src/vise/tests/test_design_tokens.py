"""Tests for engines.design_tokens.scan.

Four of these are regression pins for real false positives found by hand-
auditing this scanner's output against three live projects: an HTML numeric
entity read as a hex colour, a value inside a comment read as a use, a CSS
``@property`` initial-value read as a violation instead of a declaration, and
a bare ``getComputedStyle`` local pulling a business-logic ``.ts`` file into
the UI scan. Each of those shipped once; this file exists so none of them
ship again. The rest pin the spec's scenarios: the four finding kinds, the
"no UI at all" non-violation, the allowances passthrough contract, the never-
raises guarantee, and the excluded-scope list.
"""
from __future__ import annotations

from pathlib import Path

from vise.engines.design_tokens import scan

# ---------------------------------------------------------------------------
# Regression pins — real false positives found by hand-auditing output
# ---------------------------------------------------------------------------


def test_html_numeric_entity_is_not_a_raw_color(tmp_path: Path) -> None:
    (tmp_path / "Docs.tsx").write_text(
        "export const Docs = () => <code>/v1/cms/&#123;slug&#125;/products</code>;\n",
        encoding="utf-8",
    )

    report = scan(tmp_path)

    color_findings = [f for f in report.findings if f.kind == "raw_color"]
    assert color_findings == []


def test_value_inside_multiline_comment_is_not_a_use_and_line_numbers_survive(
    tmp_path: Path,
) -> None:
    css = (
        "/*\n"
        "color: #111111;\n"
        "color: #a15c07;\n"
        "*/\n"
        "color: #3355ff;\n"
    )
    (tmp_path / "styles.css").write_text(css, encoding="utf-8")

    report = scan(tmp_path)

    color_findings = [f for f in report.findings if f.kind == "raw_color"]
    literals_reported = {f.detail for f in color_findings}
    assert not any("#111111" in d for d in literals_reported)
    assert not any("#a15c07" in d for d in literals_reported)
    assert len(color_findings) == 1
    assert color_findings[0].line == 5
    assert "#3355ff" in color_findings[0].detail


def test_property_initial_value_is_a_declaration_not_a_violation(tmp_path: Path) -> None:
    css = (
        "@property --brand-color {\n"
        "  syntax: '<color>';\n"
        "  initial-value: #abcabc;\n"
        "  inherits: false;\n"
        "}\n"
    )
    (tmp_path / "brand.css").write_text(css, encoding="utf-8")

    report = scan(tmp_path)

    color_findings = [f for f in report.findings if f.kind == "raw_color"]
    assert color_findings == []


def test_get_computed_style_local_does_not_pull_ts_file_into_ui_scan(tmp_path: Path) -> None:
    ts = (
        "export function readColor(el: HTMLElement): string {\n"
        "  const style = getComputedStyle(el);\n"
        "  return style.color || '#3355ff';\n"
        "}\n"
    )
    (tmp_path / "domUtils.ts").write_text(ts, encoding="utf-8")

    report = scan(tmp_path)

    assert report.ui_files_scanned == 0
    assert report.findings == []


# ---------------------------------------------------------------------------
# Spec scenarios
# ---------------------------------------------------------------------------


def test_stray_hex_color_in_component_reports_file_line_and_literal(tmp_path: Path) -> None:
    (tmp_path / "colors.css").write_text(
        ":root {\n  --color-primary: #112233;\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "Button.tsx").write_text(
        "export function Button() {\n"
        "  return <div style={{color: '#3355ff'}}>Click</div>;\n"
        "}\n",
        encoding="utf-8",
    )

    report = scan(tmp_path)

    color_findings = [f for f in report.findings if f.kind == "raw_color"]
    assert len(color_findings) == 1
    assert color_findings[0].path == "Button.tsx"
    assert color_findings[0].line == 2
    assert "#3355ff" in color_findings[0].detail


def test_font_size_tokens_bypassed_by_arbitrary_literals_reports_both_counts(
    tmp_path: Path,
) -> None:
    css = (
        ":root {\n"
        "  --text-xs: 12px;\n"
        "  --text-sm: 14px;\n"
        "  --text-base: 16px;\n"
        "  --text-lg: 18px;\n"
        "  --text-xl: 20px;\n"
        "  --text-2xl: 24px;\n"
        "}\n"
        ".a { font-size: 11px; }\n"
        ".b { font-size: 13px; }\n"
        ".c { font-size: 17px; }\n"
        ".d { font-size: 19px; }\n"
    )
    (tmp_path / "typography.css").write_text(css, encoding="utf-8")

    report = scan(tmp_path)

    bypassed = [f for f in report.findings if f.kind == "scale_bypassed"]
    assert len(bypassed) == 1
    assert bypassed[0].detail == (
        "6 type tokens declared, 0 referenced elsewhere; 4 arbitrary "
        "font-size literals used instead"
    )


def test_no_font_family_declared_anywhere_reports_finding(tmp_path: Path) -> None:
    (tmp_path / "layout.css").write_text(
        ".page { color: red; }\n",
        encoding="utf-8",
    )

    report = scan(tmp_path)

    no_font = [f for f in report.findings if f.kind == "no_font_family"]
    assert len(no_font) == 1
    assert no_font[0].path == ""
    assert no_font[0].line == 0


def test_disciplined_project_reports_zero_findings(tmp_path: Path) -> None:
    css = (
        ":root {\n"
        "  --color-primary: #112233;\n"
        "  --spacing-md: 16px;\n"
        "}\n"
        ".box {\n"
        "  color: var(--color-primary);\n"
        "  padding: 16px;\n"
        "  font-family: 'Inter', sans-serif;\n"
        "}\n"
    )
    (tmp_path / "styles.css").write_text(css, encoding="utf-8")

    report = scan(tmp_path)

    assert report.findings == []


def test_no_ui_source_at_all_is_not_a_violation(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")

    report = scan(tmp_path)

    assert report.ui_files_scanned == 0
    assert report.findings == []


def test_allowances_are_not_applied_by_scan(tmp_path: Path) -> None:
    (tmp_path / "styles.css").write_text(
        ".box { color: #3355ff; }\n",
        encoding="utf-8",
    )

    unfiltered = scan(tmp_path)
    with_allowances = scan(tmp_path, allowances={"raw_color": 100})

    assert len(with_allowances.findings) == len(unfiltered.findings)
    assert any(f.kind == "raw_color" for f in with_allowances.findings)


# ---------------------------------------------------------------------------
# Never raises
# ---------------------------------------------------------------------------


def test_undecodable_binary_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "sprite.css").write_bytes(b"\xff\xfe\x00\x01broken")

    report = scan(tmp_path)

    assert isinstance(report.findings, list)


def test_nonexistent_directory_returns_empty_report(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    report = scan(missing)

    assert report.ui_files_scanned == 0
    assert report.findings == []


def test_unreadable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    css = tmp_path / "styles.css"
    css.write_text(".box { color: #3355ff; }\n", encoding="utf-8")
    css.chmod(0o000)

    try:
        report = scan(tmp_path)
    finally:
        css.chmod(0o644)  # restore so tmp_path cleanup can remove it

    assert isinstance(report.findings, list)


# ---------------------------------------------------------------------------
# Excluded scopes
# ---------------------------------------------------------------------------


def test_node_modules_is_excluded(tmp_path: Path) -> None:
    nested = tmp_path / "node_modules" / "pkg"
    nested.mkdir(parents=True)
    (nested / "styles.css").write_text(".box { color: #3355ff; }\n", encoding="utf-8")

    report = scan(tmp_path)

    assert report.ui_files_scanned == 0


def test_dist_is_excluded(tmp_path: Path) -> None:
    nested = tmp_path / "dist"
    nested.mkdir()
    (nested / "bundle.css").write_text(".box { color: #3355ff; }\n", encoding="utf-8")

    report = scan(tmp_path)

    assert report.ui_files_scanned == 0


def test_min_css_is_excluded(tmp_path: Path) -> None:
    (tmp_path / "bundle.min.css").write_text(".box { color: #3355ff; }\n", encoding="utf-8")

    report = scan(tmp_path)

    assert report.ui_files_scanned == 0


def test_test_tsx_is_excluded(tmp_path: Path) -> None:
    (tmp_path / "Button.test.tsx").write_text(
        "test('renders', () => { expect('#3355ff').toBe('#3355ff'); });\n",
        encoding="utf-8",
    )

    report = scan(tmp_path)

    assert report.ui_files_scanned == 0


# ---------------------------------------------------------------------------
# git check-ignore usage must not gate the scan on git availability
# ---------------------------------------------------------------------------


def test_non_git_repo_still_scans(tmp_path: Path) -> None:
    # tmp_path is deliberately not a git repository — no `git init` here.
    (tmp_path / "styles.css").write_text(".box { color: #3355ff; }\n", encoding="utf-8")

    report = scan(tmp_path)

    assert report.ui_files_scanned == 1
    assert any(f.kind == "raw_color" for f in report.findings)


def test_stylesheet_named_tokens_is_parsed_as_css(tmp_path: Path) -> None:
    """A `.css` file is never treated as a JS token-config module.

    `_CONFIG_FILE_RE` matches the filename `tokens.css`, and the config path
    parses a `fontSize: {...}` object literal — nothing a stylesheet contains.
    The shortcut therefore skipped the CSS declaration parser outright, and the
    most obvious filename a design system has reported zero declared tokens.
    """
    body = ":root {\n  --color-brand: #3355ff;\n  --text-sm: 14px;\n}\n"
    (tmp_path / "tokens.css").write_text(body)
    named = scan(tmp_path)

    (tmp_path / "tokens.css").unlink()
    (tmp_path / "palette.css").write_text(body)
    neutral = scan(tmp_path)

    assert named.tokens_declared == neutral.tokens_declared
    assert named.tokens_declared > 0
