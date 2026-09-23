#!/usr/bin/env bash
# scripts/install.sh — install slimtoken (pure Python) + wire ANTHROPIC_BASE_URL.
# Reversible: `scripts/uninstall.sh` (or `slimtoken uninstall`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pip install -e "$HERE" >/dev/null
python3 "$HERE/tools/build_opt.py"  # Cython by default; skips gracefully
slimtoken install "$@"
echo
echo "Done. slimtoken is installed."
echo "The proxy is the default — see README Quick Start: 'slimtoken serve --upstream <your model API>'."
echo "Disable anytime: SLIMTOKEN_MINIFY=0, or 'slimtoken uninstall'."