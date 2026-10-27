#!/usr/bin/env bash




set -euo pipefail

slimtoken uninstall "$@"
pip uninstall -y slimtoken >/dev/null 2>&1 || true
echo "slimtoken removed. ANTHROPIC_BASE_URL restored/unset."