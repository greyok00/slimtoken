#!/usr/bin/env bash
# scripts/build-speed.sh — OPTIONAL: compile slimtoken to native code (Cython .so) for speed.
#
# slimtoken runs as pure Python by default (portable to any Python 3.9+ / any OS).
# This script compiles every module to a native .so ON YOUR MACHINE for YOUR
# Python version, then reinstalls the compiled package. The .so is faster on the
# minify hot path. Because native code is Python-ABI-specific, it must be built
# per machine — that's why this is a local script, not a distributed artifact.
#
# Needs: gcc (or clang) + Cython. This script installs Cython if missing.
# Reversible: re-run `pip install --force-reinstall --no-deps .` (without the env)
#             to restore pure Python.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# pip wrapper that transparently handles PEP 668 "externally-managed" envs.
_pip() {
    pip install "$@" || pip install --break-system-packages "$@"
}

echo "==> slimtoken speed build (Cython, local compile for this Python)"
python3 -c "import Cython" 2>/dev/null || {
    echo "==> Cython not found — installing Cython>=3.0"
    _pip "Cython>=3.0"
}

echo "==> compiling + reinstalling (SLIMTOKEN_BUILD_CYTHON=1, --no-build-isolation)"
# --no-build-isolation: build in THIS env (where Cython is installed above) rather
# than an isolated env that would lack Cython (it's an optional extra, not a
# build-system requirement, so pure-Python installs stay Cython-free).
SLIMTOKEN_BUILD_CYTHON=1 _pip --no-build-isolation --force-reinstall --no-deps "$HERE" >/dev/null

echo "==> verifying .so for every module"
SITE=$(python3 -c "import slimtoken, os; print(os.path.dirname(slimtoken.__file__))")
missing=0
for m in __init__ pipeline tool_minify system_minify message_minify dedup_tool_results \
         distill_old_turns token_budget context_prune upstream tls proxy \
         config_optimizer cli lazy_mcp; do
    if ! ls "$SITE"/${m}*.so >/dev/null 2>&1; then
        echo "  ✗ missing .so: $m"; missing=$((missing+1))
    fi
done
if [ "$missing" -ne 0 ]; then
    echo "!! $missing module(s) did not compile to .so — speed build incomplete"
    exit 1
fi
so_count=$(ls "$SITE"/*.so 2>/dev/null | wc -l)
echo "  ✓ $so_count .so built (15 expected)"

echo
echo "Done. slimtoken is now running as compiled native code."
echo "See the speedup:  python3 $HERE/bench/benchmark.py"
echo "Restore pure Python:  pip install --force-reinstall --no-deps \"$HERE\""
echo "  (add --break-system-packages on Debian/Ubuntu managed Python)"