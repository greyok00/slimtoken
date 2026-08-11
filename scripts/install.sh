#!/usr/bin/env bash
# scripts/install.sh — install slimtoken (pure Python) + wire ANTHROPIC_BASE_URL.
# Reversible: `scripts/uninstall.sh` (or `slimtoken uninstall`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pip install "$HERE" >/dev/null
slimtoken install "$@"
echo
echo "Done. slimtoken is installed."
echo "The proxy is the default — start it and every request is minified:"
echo "  slimtoken serve --upstream http://127.0.0.1:8080        # local llama-server"
echo "  slimtoken serve --upstream https://api.anthropic.com  # cloud"
echo "ANTHROPIC_BASE_URL is already wired to the proxy (see ~/.slimtoken/prev_env)."
echo "Disable anytime: SLIMTOKEN_MINIFY=0, or 'slimtoken uninstall'."