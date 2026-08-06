#!/usr/bin/env bash
# scripts/install.sh — install slimtoken (pure Python) + wire ANTHROPIC_BASE_URL.
# Reversible: `scripts/uninstall.sh` (or `slimtoken uninstall`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pip install "$HERE" >/dev/null
slimtoken install "$@"
echo
echo "Done. slimtoken is installed."
echo "Start the proxy with:  slimtoken serve"
echo "  (local: --upstream http://127.0.0.1:8080 | cloud: --upstream https://api.anthropic.com)"