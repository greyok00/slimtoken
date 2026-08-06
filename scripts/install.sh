#!/usr/bin/env bash
# scripts/install.sh — install slimtoken as COMPILED native code + wire ANTHROPIC_BASE_URL.
# Builds the Cython .so (requires gcc + Cython; `pip install` pulls Cython via build deps),
# installs the compiled package (non-editable, so the protected .so is what runs), and
# points ANTHROPIC_BASE_URL at the proxy.
# Reversible: `scripts/uninstall.sh` (or `slimtoken uninstall`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Compiled (non-editable) install — builds .so via Cython; .py source is not what runs.
pip install "$HERE" >/dev/null
slimtoken install "$@"
echo
echo "Done. slimtoken is installed as compiled native code (.so)."
echo "Start the proxy with:  slimtoken serve"
echo "  (local: --upstream http://127.0.0.1:8080 | cloud: --upstream https://api.anthropic.com)"