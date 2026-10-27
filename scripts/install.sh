#!/usr/bin/env bash


set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pip install -e "$HERE" >/dev/null
slimtoken install "$@"
echo
echo "Done. slimtoken is installed."
echo "The proxy is the default — see README Quick Start: 'slimtoken serve --upstream <your model API>'."
echo "Disable anytime: SLIMTOKEN_MINIFY=0, or 'slimtoken uninstall'."