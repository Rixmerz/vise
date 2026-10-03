#!/usr/bin/env bash
# vise installer — registers this repo as a local Claude Code plugin
# marketplace and installs the vise plugin. Idempotent: safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Same data-dir rule as vise.hooks._xdg and bin/vise-run: honor XDG_DATA_HOME
# only when set AND absolute. An existing venv at the legacy location wins, so
# re-running this on a pre-XDG install upgrades it in place instead of building
# a second venv and orphaning the first.
if [ -n "${XDG_DATA_HOME:-}" ] && [ "${XDG_DATA_HOME#/}" != "${XDG_DATA_HOME}" ]; then
  DATA_DIR="${XDG_DATA_HOME}/vise"
else
  DATA_DIR="${HOME}/.local/share/vise"
fi
if [ -x "${HOME}/.local/share/vise/venv/bin/python" ]; then
  VENV_DIR="${HOME}/.local/share/vise/venv"
else
  VENV_DIR="${DATA_DIR}/venv"
fi

DEV=0
DESIGN=0
MOD=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --design) DESIGN=1 ;;
    --mod) MOD=1 ;;
    -h|--help)
      cat <<'USAGE'
usage: ./install.sh [--dev] [--design] [--mod]

  --dev     also install the [dev] extras (pytest, ruff, mypy, coverage)
  --design  also install the [design] extra and a Chromium for it. The three
            render gates (ui_layout, ui_contrast, design_tokens) fail CLOSED,
            so without this they refuse every run in a repo that wires them.
            Left opt-in because it downloads a browser (~150MB) and most repos
            never turn those gates on.
  --mod     also install vise-mod, the in-process companion: the phase on the
            status line and in a pane, the phase in the system prompt, and
            tasky's failed fixes in a debug session. Left opt-in because a mod
            runs inside Claude Code with no sandbox, and needs Claude Code
            2.1.287 or later.
USAGE
      exit 0
      ;;
  esac
done

# 1. claude CLI must exist
if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not found in PATH." >&2
  echo "Install Claude Code first: https://claude.com/claude-code" >&2
  exit 1
fi

# 2. Runtime deps: ensure a python with fastmcp + fastembed.
#    bin/vise-run prefers ${VENV_DIR}/bin/python, so we install there
#    if system python3 lacks the deps.
if python3 -c "import fastmcp, fastembed" >/dev/null 2>&1; then
  echo "ok: system python3 has vise runtime deps."
else
  echo "System python3 lacks fastmcp/fastembed — using dedicated venv."
  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
  fi
  "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
  "${VENV_DIR}/bin/pip" install --quiet -e "$REPO_DIR"
  "${VENV_DIR}/bin/python" -c "import fastmcp, fastembed" \
    || { echo "error: venv install failed (fastmcp/fastembed still unimportable)." >&2; exit 1; }
  echo "ok: venv ready at ${VENV_DIR}."
fi

# 2b. Dev extras (--dev): whatever [dev] lists in pyproject.toml, into the venv.
if [ "$DEV" = "1" ]; then
  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
  fi
  "${VENV_DIR}/bin/pip" install --quiet -e "${REPO_DIR}[dev]"
  echo "ok: dev extras installed into ${VENV_DIR}."
fi

# 2c. Design extras (--design): playwright plus the browser it drives.
#
#     Both steps, and in this order. Installing the extra alone leaves
#     playwright installed with no browser, and the gates then fail closed on a
#     message about a missing Chromium — one install later the person is stuck
#     again with no idea why. That two-step trap is exactly what
#     render_harness._unavailable_message exists to spell out; doing both here
#     means nobody has to read it.
#
#     `playwright install` MUST run through this venv's own interpreter: a
#     `playwright` binary from another environment installs a build this
#     playwright will refuse.
if [ "$DESIGN" = "1" ]; then
  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
  fi
  "${VENV_DIR}/bin/pip" install --quiet -e "${REPO_DIR}[design]"
  "${VENV_DIR}/bin/python" -m playwright install chromium
  echo "ok: render-gate browser installed for ${VENV_DIR}."
  echo "    layout-inspector, if you use it, resolves its OWN chromium — its"
  echo "    playwright will refuse a build installed by a different one."
fi

# 3. Register this CLONE as a local marketplace and install from it.
#
#    The name is `vise-dev`, not `rixmerz`. Claude Code keys marketplaces by
#    NAME across all sources, so a repo that declares a name someone else
#    already uses silently displaces theirs and every plugin from the old one
#    stops resolving. That is not hypothetical: this repo briefly declared
#    `rixmerz`, which is the owner namespace published at
#    github.com/Rixmerz/claude-plugins, and it knocked livespec@rixmerz offline.
#
#    Published installs come from that index (`vise@rixmerz`). This script is
#    the from-a-clone path, so it gets its own namespace and cannot collide.
MARKETPLACE="vise-dev"

if claude plugin marketplace list 2>/dev/null | grep -q "${MARKETPLACE}"; then
  claude plugin marketplace update "$MARKETPLACE" || true
else
  claude plugin marketplace add "$REPO_DIR"
fi

# Reinstall rather than update, because `claude plugin update` compares the
# *version* in plugin.json and short-circuits when it matches — and a dev
# checkout pulls a hundred changes between version bumps.
#
# This is the second time this promise broke. The first fix added the `update`
# call to replace a bare "already installed" that did no work; the update then
# did no work either, for the same reason one layer down, and the installed copy
# under ~/.claude/plugins/cache silently stayed at whatever commit it was first
# installed from. Anything that reads the plugin — every skill, every agent,
# every hook — was reading that stale copy while `vise doctor`, which runs from
# the venv's editable install, reported the working tree. The two disagreeing is
# exactly how a fixed bug looks unfixed.
#
# Both plugins declare `userConfig`, so an uninstall may take the stored option
# values with it. They live under `pluginConfigs` in ~/.claude/settings.json:
# keep a copy of the plugin's entry and put it back if the reinstall left none.
# Values in the secure store (neither plugin has one) are not in that file.
SETTINGS="${HOME}/.claude/settings.json"
_options() {  # print the plugin's pluginConfigs entry as JSON, or nothing
  [ -f "$SETTINGS" ] || return 0
  python3 - "$SETTINGS" "$1" <<'PY' 2>/dev/null || true
import json, sys
try:
    configs = json.load(open(sys.argv[1])).get("pluginConfigs") or {}
except Exception:
    configs = {}
entry = configs.get(sys.argv[2])
print(json.dumps(entry) if entry else "")
PY
}
_restore_options() {  # $1 plugin id, $2 the JSON _options printed
  [ -n "$2" ] || return 0
  python3 - "$SETTINGS" "$1" "$2" <<'PY' || echo "warn: could not restore $1's options; set them again in /plugin" >&2
import json, os, sys, tempfile
path, plugin, saved = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
data = json.load(open(path)) if os.path.exists(path) else {}
configs = data.setdefault("pluginConfigs", {})
if plugin not in configs:
    configs[plugin] = saved
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
PY
}
_install() {  # $1 plugin name; installs or reinstalls <name>@$MARKETPLACE
  local id="$1@${MARKETPLACE}"
  if claude plugin list 2>/dev/null | grep -q "$id"; then
    local saved
    saved="$(_options "$id")"
    claude plugin uninstall "$id" >/dev/null 2>&1 || true
    rm -rf "${HOME}/.claude/plugins/cache/${MARKETPLACE}/$1"
    claude plugin install "$id" --yes || return 1
    _restore_options "$id" "$saved"
    echo "ok: $1 reinstalled from this checkout — restart Claude Code to apply."
  else
    claude plugin install "$id" --yes || return 1
  fi
}

_install vise

# 3b. vise-mod (--mod). A mod runs inside Claude Code with the same reach as
#     Claude Code itself, so it is never installed unasked.
if [ "$MOD" = 1 ]; then
  if ! _install vise-mod; then
    echo "warn: vise-mod did not install. Mods need Claude Code 2.1.287 or later." >&2
  fi
fi

# 4. LSP binaries: vise declares NO language servers — the official
#    marketplace ships one plugin per language, and `lspServers` has no
#    priority field, so bundling a second claimant for an extension made
#    resolution undefined for anyone who had both. What `doctor` reports here
#    is what the plugins on THIS machine declare, and whether their binaries
#    actually start. Each starts lazily, only when a file of its type is
#    opened.
#
#    Report through `vise doctor` rather than re-deriving the list here. The
#    hint table used to be duplicated in this script, which meant two places to
#    keep in step — and this copy only ran `command -v`, so it printed a green
#    tick for `rust-analyzer` when rustup had installed a shim that exits with
#    "Unknown binary" the moment anything runs it. `vise doctor` starts each
#    server the way Claude Code does and reports what actually happened.
#    Four sections, not one. LSP servers are the dormant kind — absent means
#    nothing until you open that language. The render gates are the opposite:
#    they fail CLOSED, so a missing browser refuses every run in a repo that
#    wires them, and that belongs in front of someone who has just installed.
#    The neighbours are neither: vise cannot see whether they are mounted, so
#    what it reports is the version its guidance assumes.
#
#    The conflict section is here because this script is the moment vise joins
#    whatever else the user already has. Nothing in Claude Code arbitrates two
#    plugins claiming one extension, so a collision introduced by this install
#    is silent, and the install is exactly when it is cheapest to undo.
VISE_BIN=""
if [ -x "${VENV_DIR}/bin/vise" ]; then
  VISE_BIN="${VENV_DIR}/bin/vise"
elif command -v vise >/dev/null 2>&1; then
  VISE_BIN="vise"
fi

echo
if [ -n "$VISE_BIN" ]; then
  DOCTOR_OUT="$("$VISE_BIN" doctor 2>/dev/null || true)"
  for section in "LSP servers" "LSP extension conflicts" "Render gates" "Neighbouring MCP servers"; do
    printf '%s\n' "$DOCTOR_OUT" | sed -n "/=== ${section}/,/^\$/p"
  done
else
  echo "run \`vise doctor\` to see language servers, the render-gate browser,"
  echo "and the versions vise's guidance assumes of its neighbouring servers."
fi
echo "  (a missing LSP server stays dormant until you open that language;"
echo "   a missing render-gate browser does not — those gates fail closed.)"
echo "  (vise declares no language servers of its own: install the official"
echo "   plugin for your language, e.g. pyright-lsp@claude-plugins-official.)"

echo
echo "vise installed. Restart Claude Code (or start a new session) to load it."
