#!/usr/bin/env bash
# scripts/uninstall.sh — clean uninstall for slimtoken.
# Restores the prior ANTHROPIC_BASE_URL, removes the rc marker block, then
# uninstalls the compiled package. The agent's settings.json / CLAUDE.md / mcp.json
# are never touched.
set -euo pipefail

slimtoken uninstall "$@"
pip uninstall -y slimtoken >/dev/null 2>&1 || true
echo "slimtoken removed. ANTHROPIC_BASE_URL restored/unset."