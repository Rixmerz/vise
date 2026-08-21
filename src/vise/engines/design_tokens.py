"""Scan UI source for design tokens declared and then bypassed at the call site.

Measured on three real projects: the failure mode is never "no tokens exist"
— it is a handful of tokens declared once and then ignored everywhere a
literal was faster to type. ``notbusy/ui`` (80 tokens, zero stray hex) proves
the discipline is achievable; ``wrap`` (44 hardcoded colours vs 25 tokens, raw
padding at 5/7/9/11px) and ``SpeedRunners-landing`` (6 font-size tokens used
twice against 17 arbitrary ``text-[Npx]`` values) show what drift looks like.

Pure stdlib, regex-based. Never raises — an unreadable file is skipped, not
fatal, because this runs inside a gate.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

UI_EXTENSIONS = {
    ".css", ".scss", ".sass", ".less",
    ".html", ".astro", ".vue", ".svelte",
    ".tsx", ".jsx", ".ts", ".js",
}
# Extensions that only count as UI source when the file actually carries
# styling signal — a .ts constants file with a hex string is not a UI bug.
_SIGNAL_REQUIRED_EXTENSIONS = {".ts", ".js"}

_SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", "out", "coverage",
    "vendor", "__pycache__", ".venv", "_fresh", "cov_profile",
}
_GENERATED_MARKERS = (".min.", ".generated.", ".gen.")
# A test file asserts *about* the UI (e.g. "this string is absent from that
# component") — it is not a render call site, so a literal inside it is not
# a design-token bypass.
_TEST_MARKERS = (".test.", ".spec.")
_TEST_DIRS = {"tests", "test", "__tests__"}

# `style\s*=\s*[{"']` requires the JSX/HTML attribute shape (`style={` or
# `style="`) — a bare `style\s*=` also matched `const style = getComputedStyle(...)`,
# a plain local variable, pulling business-logic files into the UI scan.
_STYLING_SIGNAL_RE = re.compile(r"className\s*=|style\s*=\s*[{\"']|\.css['\"]|styled\.|css`")
# A file whose whole purpose is declaring tokens (tailwind config, a theme or
# tokens module) — colours/sizes here are declarations, never call-site findings.
_CONFIG_FILE_RE = re.compile(r"(tailwind\.config\.|theme\.|tokens\.)", re.IGNORECASE)

_TOKEN_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
# `@property --name { ...; initial-value: X; }` is the other CSS syntax that
# declares a custom property's value — same declaration status as `--name: X;`.
_INITIAL_VALUE_RE = re.compile(r"initial-value\s*:\s*([^;]+);", re.IGNORECASE)
_TYPE_TOKEN_NAME_RE = re.compile(r"--(?:font|text)[\w-]*", re.IGNORECASE)

# Negative lookbehind excludes HTML numeric entities (&#123; &#x7B;) and any
# `#` glued to an identifier — neither is a colour literal.
_HEX_RE = re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")

# A `#abc` is only a colour when something asked for one. Without this, an
# anchor (`href="#fff"`), a URL fragment (`url("s.svg#icon-abc")`) and free
# prose inside JSX all read as hardcoded colours — confirmed on a fixture where
# three of four reported violations were not colours at all. The guard is the
# PROPERTY, not the syntax: a colour value follows a colour-bearing property in
# both CSS (`color: #fff`) and inline JS (`color:"#fff"`).
# ponytail: a substring window rather than a parser. It misses a colour split
# across lines; switch to a real declaration scan if that ever shows up.
_COLOR_PROP_RE = re.compile(
    r'''(?:^|[;{,\s"'(])(?:-{2}[\w-]+|color|background|background-color|border|'''
    r"border-\w+|border-\w+-color|outline|outline-color|fill|stroke|"
    r"box-shadow|text-shadow|caret-color|accent-color|text-decoration-color|"
    r"column-rule|column-rule-color|stop-color|flood-color|lighting-color|"
    r"scrollbar-color|backgroundColor|borderColor|outlineColor|fillColor|"
    r"strokeColor|boxShadow|textShadow|caretColor|accentColor|shadowColor|"
    r"tintColor|placeholderTextColor|borderTopColor|borderBottomColor|"
    r"borderLeftColor|borderRightColor|gradient|linear-gradient|radial-gradient)"
    r"\s*[:=]",
    re.IGNORECASE,
)
_COLOR_WINDOW = 80


def _in_color_value_position(line: str, at: int) -> bool:
    """True when the match at ``at`` follows a colour-bearing property."""
    return bool(_COLOR_PROP_RE.search(line[max(0, at - _COLOR_WINDOW):at]))
_FUNC_COLOR_RE = re.compile(r"\b(?:rgba?|hsla?)\(\s*[\d.%\s,/-]+\)", re.IGNORECASE)

_FONT_SIZE_CSS_RE = re.compile(r"font-size\s*:\s*([\d.]+)(px|rem|em)\b", re.IGNORECASE)
_FONT_SIZE_TW_RE = re.compile(r"\btext-\[([\d.]+)(px|rem|em)]")

_SPACING_PROPS = r"(?:margin|padding|gap|row-gap|column-gap)(?:-(?:top|bottom|left|right))?"
_SPACING_CSS_RE = re.compile(rf"\b({_SPACING_PROPS})\s*:\s*([^;]+);", re.IGNORECASE)
_SPACING_TW_RE = re.compile(r"\b[mp][trblxy]?-\[([\d.]+)(px|rem)]")

_RADIUS_CSS_RE = re.compile(r"border-radius\s*:\s*([\d.]+)(px|rem|em)\b", re.IGNORECASE)
_RADIUS_TW_RE = re.compile(r"\brounded(?:-[a-z]+)?-\[([\d.]+)(px|rem)]")

_FONT_FAMILY_RE = re.compile(r"font-family\s*:", re.IGNORECASE)
_LENGTH_TOKEN_RE = re.compile(r"([\d.]+)(px|rem|em)")
_LINE_COMMENT_RE = re.compile(r"//.*$")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_LIKE_EXTENSIONS = {".html", ".astro", ".vue", ".svelte"}


@dataclass(frozen=True)
class Finding:
    """A single design-token violation."""

    kind: str  # raw_color | raw_font_size | raw_spacing | raw_radius | scale_bypassed | no_font_family
    path: str
    line: int  # 0 for a project-level finding
    detail: str


@dataclass(frozen=True)
class Report:
    """Result of a project scan."""

    findings: list[Finding]
    ui_files_scanned: int
    tokens_declared: int

    def summary(self) -> str:
        return (
            f"{len(self.findings)} findings across {self.ui_files_scanned} UI files "
            f"({self.tokens_declared} tokens declared)"
        )


@dataclass
class _ScanState:
    """Accumulators threaded through the per-file pass."""

    declared_values: set[str] = field(default_factory=set)
    declared_type_tokens: set[str] = field(default_factory=set)
    arbitrary_font_sizes: set[str] = field(default_factory=set)
    saw_font_family: bool = False
    combined_text: list[str] = field(default_factory=list)


def _gitignored(project_dir: Path, candidates: list[Path]) -> set[Path]:
    """Ask git which of *candidates* are ignored. Empty set if git is unusable."""
    try:
        rels = [str(p.relative_to(project_dir)) for p in candidates]
        # check-ignore exits 1 when nothing matches — that is not an error here.
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
            ["git", "-C", str(project_dir), "check-ignore", "-z", "--stdin"],
            # -z on both sides: a path containing a newline desynchronised the
            # line-oriented protocol, so git answered about a different file
            # and the ignore decision was silently wrong.
            input="\0".join(rels) + "\0",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return {project_dir / part for part in proc.stdout.split("\0") if part}
    except (OSError, subprocess.SubprocessError):
        return set()


def _collect_ui_files(project_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in project_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in UI_EXTENSIONS:
            continue
        # `is_file()` followed the link and said yes. Resolve and confirm the
        # target really lives in this project before reading it — CWE-59.
        if not _within_project(path, project_dir):
            continue
        rel_parts = path.relative_to(project_dir).parts[:-1]
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        if any(part in _TEST_DIRS for part in rel_parts):
            continue
        name = path.name.lower()
        if any(marker in name for marker in _GENERATED_MARKERS):
            continue
        if any(marker in name for marker in _TEST_MARKERS):
            continue
        candidates.append(path)
    if not candidates:
        return []
    ignored = _gitignored(project_dir, candidates)
    return [p for p in candidates if p not in ignored]


# A gate reads whatever the repo hands it, so the repo boundary is a trust
# boundary. `Path.is_file()` follows symlinks, so a committed `theme.css` ->
# `/etc/shadow` was read and its regex-matched fragments reached the persisted
# evidence string. Narrow oracle rather than a file dump, and still a read of a
# file nobody authorised. CWE-59.
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _within_project(path: Path, root: Path) -> bool:
    """True when *path* really lives under *root*, symlinks resolved."""
    try:
        return path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return False


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            # ponytail: skip rather than truncate. A 2 MB stylesheet is
            # generated or vendored; scanning half of one reports findings at
            # line numbers that do not survive the next build.
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _blank_out(match: re.Match[str]) -> str:
    """Replace a matched comment with newlines only, so line numbers survive."""
    return "\n" * match.group(0).count("\n")


def _strip_comments(text: str, suffix: str) -> str:
    # Whole-file pass (not per-line) so a /* ... */ or <!-- --> block spanning
    # many lines is fully removed instead of only the line it starts on.
    # Newline count is preserved so every reported line number stays correct.
    text = _BLOCK_COMMENT_RE.sub(_blank_out, text)
    if suffix in _HTML_LIKE_EXTENSIONS:
        text = _HTML_COMMENT_RE.sub(_blank_out, text)
    # `//` is line-scoped by construction — safe to strip per physical line.
    # ponytail: naive, strips a `//` inside a string/URL literal too (e.g.
    # `"http://x"`); a real tokenizer would distinguish, this scanner won't own one.
    return "\n".join(_LINE_COMMENT_RE.sub("", line) for line in text.splitlines())


def _px_equivalent(value: str, unit: str) -> float:
    number = float(value)
    if unit == "rem":
        return number * 16
    if unit == "em":
        return number * 16
    return number


def _offscale_lengths(value_text: str, declared: set[str]) -> list[str]:
    """Return the literal length tokens in *value_text* that are not on-scale."""
    offenders = []
    for match in _LENGTH_TOKEN_RE.finditer(value_text):
        literal = match.group(0)
        if literal in declared:
            continue
        px = _px_equivalent(match.group(1), match.group(2))
        if px in (0, 1, 2):
            continue
        if px % 4 == 0:
            continue
        offenders.append(literal)
    return offenders


def _extract_config_type_tokens(text: str) -> set[str]:
    """Best-effort ``fontSize: { ... }`` key extraction from a config/theme file."""
    tokens: set[str] = set()
    block = re.search(r"fontSize\s*:\s*\{([^}]*)\}", text, re.DOTALL)
    if block:
        for key in re.finditer(r"['\"]?([\w-]+)['\"]?\s*:", block.group(1)):
            tokens.add(f"text-{key.group(1)}")
    return tokens


def _process_file(  # noqa: PLR0912 — one regex-driven pass, splitting it adds indirection not clarity
    rel: str, text: str, suffix: str, is_config: bool, state: _ScanState, findings: list[Finding]
) -> None:
    state.combined_text.append(text)
    if is_config:
        state.declared_type_tokens |= _extract_config_type_tokens(text)
        return

    stripped = _strip_comments(text, suffix)
    for lineno, line in enumerate(stripped.splitlines(), start=1):
        decl = _TOKEN_DECL_RE.search(line)
        if decl:
            name, value = decl.group(1), decl.group(2).strip()
            state.declared_values.add(value)
            for length in _LENGTH_TOKEN_RE.finditer(value):
                state.declared_values.add(length.group(0))
            if _TYPE_TOKEN_NAME_RE.search(name):
                state.declared_type_tokens.add(name)
            continue  # a declaration line is never a call-site finding

        initial = _INITIAL_VALUE_RE.search(line)
        if initial:
            state.declared_values.add(initial.group(1).strip())
            continue  # @property's initial-value is a declaration too

        if _FONT_FAMILY_RE.search(line):
            state.saw_font_family = True

        for m in (*_HEX_RE.finditer(line), *_FUNC_COLOR_RE.finditer(line)):
            if not _in_color_value_position(line, m.start()):
                continue
            findings.append(Finding("raw_color", rel, lineno,
                                     f"hardcoded color {m.group(0)} — use a token"))

        for m in (*_FONT_SIZE_CSS_RE.finditer(line), *_FONT_SIZE_TW_RE.finditer(line)):
            literal = f"{m.group(1)}{m.group(2)}"
            state.arbitrary_font_sizes.add(literal)
            findings.append(Finding("raw_font_size", rel, lineno,
                                     f"literal font-size {literal} — use a named scale step"))

        for m in _SPACING_CSS_RE.finditer(line):
            prop, value = m.group(1), m.group(2)
            for literal in _offscale_lengths(value, state.declared_values):
                findings.append(Finding("raw_spacing", rel, lineno,
                                         f"{prop}: {literal} not on the 4px scale — use a spacing token"))
        for m in _SPACING_TW_RE.finditer(line):
            literal = f"{m.group(1)}{m.group(2)}"
            if _offscale_lengths(literal, state.declared_values):
                findings.append(Finding("raw_spacing", rel, lineno,
                                         f"arbitrary spacing {literal} not on the 4px scale — use a spacing token"))

        for m in (*_RADIUS_CSS_RE.finditer(line), *_RADIUS_TW_RE.finditer(line)):
            literal = f"{m.group(1)}{m.group(2)}"
            if _offscale_lengths(literal, state.declared_values):
                findings.append(Finding("raw_radius", rel, lineno,
                                         f"literal border-radius {literal} — use a radius token"))


def scan(project_dir: str | Path, allowances: dict[str, int] | None = None) -> Report:
    """Scan *project_dir* for design-token declarations bypassed at call sites.

    Always returns raw findings; ``allowances`` is the caller's ratchet
    (kind -> tolerated count) and is not applied here. Never raises: an
    unreadable file is skipped rather than aborting the scan.
    """
    del allowances  # comparison belongs to the caller, per spec
    root = Path(project_dir)
    ui_files = _collect_ui_files(root)
    if not ui_files:
        return Report(findings=[], ui_files_scanned=0, tokens_declared=0)

    state = _ScanState()
    findings: list[Finding] = []
    scanned = 0

    for path in ui_files:
        text = _read_text(path)
        if text is None:
            continue
        suffix = path.suffix.lower()
        is_config = bool(_CONFIG_FILE_RE.search(path.name))
        if suffix in _SIGNAL_REQUIRED_EXTENSIONS and not is_config:
            if not _STYLING_SIGNAL_RE.search(text):
                continue
        rel = str(path.relative_to(root))
        _process_file(rel, text, suffix, is_config, state, findings)
        scanned += 1

    if scanned == 0:
        return Report(findings=[], ui_files_scanned=0, tokens_declared=0)

    tokens_declared = len(state.declared_values)

    if state.declared_type_tokens:
        combined = "\n".join(state.combined_text)
        # `combined.count(name)` counted SUBSTRINGS: `--text-s` matches inside
        # `--text-sm`, so one token name being a prefix of another inflated
        # `referenced` and could suppress a real `scale_bypassed`. Match the
        # whole token name instead.
        referenced = sum(
            1 for name in state.declared_type_tokens
            if len(re.findall(rf"{re.escape(name)}(?![\w-])", combined)) > 1
        )
        arbitrary_count = len(state.arbitrary_font_sizes)
        if arbitrary_count > referenced:
            findings.append(Finding(
                "scale_bypassed", "", 0,
                f"{len(state.declared_type_tokens)} type tokens declared, "
                f"{referenced} referenced elsewhere; {arbitrary_count} arbitrary "
                "font-size literals used instead",
            ))

    if not state.saw_font_family:
        findings.append(Finding(
            "no_font_family", "", 0,
            "no font-family declared anywhere — shipping the framework default face",
        ))

    return Report(findings=findings, ui_files_scanned=scanned, tokens_declared=tokens_declared)
