#!/usr/bin/env bash
# vise installer — registers this repo as a local Claude Code plugin
# marketplace and installs the vise plugin. Idempotent: safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${HOME}/.local/share/vise/venv"

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

# 3. Register marketplace + install plugin (idempotent).
if claude plugin marketplace list 2>/dev/null | grep -q '^vise\b\|"name": *"vise"\|vise '; then
  claude plugin marketplace update vise || true
else
  claude plugin marketplace add "$REPO_DIR"
fi

if claude plugin list 2>/dev/null | grep -q 'vise'; then
  echo "ok: vise plugin already installed."
else
  claude plugin install vise@vise
fi

echo
echo "vise installed. Restart Claude Code (or start a new session) to load it."
