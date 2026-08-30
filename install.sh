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
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
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

if claude plugin list 2>/dev/null | grep -q "vise@${MARKETPLACE}"; then
  # Already installed. Reinstall rather than update, because `claude plugin
  # update` compares the *version* in plugin.json and short-circuits when it
  # matches — and a dev checkout pulls a hundred changes between version bumps.
  #
  # This is the second time this promise broke. The first fix added the
  # `update` call to replace a bare "already installed" that did no work; the
  # update then did no work either, for the same reason one layer down, and the
  # installed copy under ~/.claude/plugins/cache silently stayed at whatever
  # commit it was first installed from. Anything that reads the plugin — every
  # skill, every agent, the lspServers map — was reading that stale copy while
  # `vise doctor`, which runs from the venv's editable install, reported the
  # working tree. The two disagreeing is exactly how a fixed bug looks unfixed.
  #
  # Uninstall-then-install is safe here: vise declares no `userConfig`, so
  # there are no stored option values for the uninstall to drop.
  claude plugin uninstall "vise@${MARKETPLACE}" >/dev/null 2>&1 || true
  rm -rf "${HOME}/.claude/plugins/cache/${MARKETPLACE}/vise"
  claude plugin install "vise@${MARKETPLACE}" --yes
  echo "ok: vise plugin reinstalled from this checkout — restart Claude Code to apply."
else
  claude plugin install "vise@${MARKETPLACE}" --yes
fi

# 4. LSP binaries: vise declares language servers for 12 ecosystems in
#    plugin.json but does NOT ship the binaries. Each starts lazily, only when
#    a file of its type is opened.
#
#    Report through `vise doctor` rather than re-deriving the list here. The
#    hint table used to be duplicated in this script, which meant two places
#    to keep in step with plugin.json — and this copy only ran `command -v`,
#    so it printed a green tick for `rust-analyzer` when rustup had installed
#    a shim that exits with "Unknown binary" the moment anything runs it.
#    `vise doctor` starts each server the way Claude Code does and reports
#    what actually happened.
echo
if [ -x "${VENV_DIR}/bin/vise" ]; then
  "${VENV_DIR}/bin/vise" doctor 2>/dev/null | sed -n '/LSP servers/,/^$/p'
elif command -v vise >/dev/null 2>&1; then
  vise doctor 2>/dev/null | sed -n '/LSP servers/,/^$/p'
else
  echo "LSP servers: run \`vise doctor\` to see which are installed."
fi
echo "  (a missing server stays dormant until you open that language.)"

echo
echo "vise installed. Restart Claude Code (or start a new session) to load it."
