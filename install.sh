#!/usr/bin/env bash
# vise installer — registers this repo as a local Claude Code plugin
# marketplace and installs the vise plugin. Idempotent: safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${HOME}/.local/share/vise/venv"

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

# 2b. Dev extras (--dev): pytest, pytest-asyncio, ruff into the venv.
if [ "$DEV" = "1" ]; then
  if [ ! -x "${VENV_DIR}/bin/python" ]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    python3 -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
  fi
  "${VENV_DIR}/bin/pip" install --quiet -e "${REPO_DIR}[dev]"
  echo "ok: dev extras installed into ${VENV_DIR}."
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

# 4. LSP binaries: vise declares language servers for 12 ecosystems in
#    plugin.json, but does NOT ship the binaries. Each server starts lazily
#    (only when a file of its type is opened) and is a no-op if its command is
#    absent (strict:false). Report which are present and how to get the rest.
echo
echo "LSP language servers (declared by vise, installed separately):"
_lsp_hints=(
  "rust-analyzer:rustup component add rust-analyzer"
  "gopls:go install golang.org/x/tools/gopls@latest"
  "pyright-langserver:npm i -g pyright"
  "typescript-language-server:npm i -g typescript typescript-language-server"
  "clangd:brew install llvm  # or apt install clangd"
  "intelephense:npm i -g intelephense"
  "ruby-lsp:gem install ruby-lsp"
  "lua-language-server:brew install lua-language-server"
  "jdtls:brew install jdtls"
  "kotlin-lsp:see github.com/Kotlin/kotlin-lsp"
  "sourcekit-lsp:ships with the Swift toolchain (swift.org)"
  "csharp-ls:dotnet tool install --global csharp-ls"
)
_missing=0
for entry in "${_lsp_hints[@]}"; do
  cmd="${entry%%:*}"; hint="${entry#*:}"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '  \033[32m✓\033[0m %s\n' "$cmd"
  else
    printf '  \033[33m·\033[0m %-28s missing — %s\n' "$cmd" "$hint"
    _missing=$((_missing + 1))
  fi
done
if [ "$_missing" -gt 0 ]; then
  echo "  ($_missing not installed — that's fine; each stays dormant until you open that language.)"
fi

echo
echo "vise installed. Restart Claude Code (or start a new session) to load it."
