"""setup.py — slimtoken build.

By default slimtoken installs as PURE PYTHON (.py) — portable across any
Python 3.9+ on any OS. That is the recommended, works-anywhere default.

For an OPTIONAL speed build, compile every module to a native extension
(.so/.pyd) with Cython by setting SLIMTOKEN_BUILD_CYTHON=1 at install time, or
run `scripts/build-speed.sh` which does it for you:

    SLIMTOKEN_BUILD_CYTHON=1 pip install .          # compiled .so install
    bash scripts/build-speed.sh                      # same, plus verification

The compiled build is faster on the hot path but is, like all native code,
specific to the Python version + OS it was built on — so it compiles on your
own machine for your Python. The pure-Python default remains the portable
baseline. Cython is NOT required for the default install.
"""
import os
from setuptools import setup, Extension

# Every module in the package — compiled to native code only in the speed build.
MODS = [
    "__init__", "pipeline", "tool_minify", "system_minify", "message_minify",
    "dedup_tool_results", "distill_old_turns", "token_budget", "context_prune",
    "upstream", "tls", "proxy", "config_optimizer", "cli", "lazy_mcp",
]

_BUILD_CYTHON = os.environ.get("SLIMTOKEN_BUILD_CYTHON", "").lower() in ("1", "true", "yes", "on")

ext_modules = []
if _BUILD_CYTHON:
    try:
        from Cython.Build import cythonize
    except ImportError as e:
        raise SystemExit(
            "[slimtoken] SLIMTOKEN_BUILD_CYTHON=1 requires Cython. Install it:  "
            "pip install \"Cython>=3.0\"") from e
    extensions = [Extension("slimtoken.%s" % m, ["src/slimtoken/%s.py" % m]) for m in MODS]
    try:
        ext_modules = cythonize(
            extensions,
            language_level="3",
            compiler_directives={
                "language_level": 3,
                "embedsignature": True,
                "always_allow_keywords": True,
            },
        )
    except Exception as e:
        raise SystemExit("[slimtoken] Cython compilation failed: %s" % e)
# else: pure-Python install — no extensions, no Cython needed.

setup(
    package_dir={"": "src"},
    packages=["slimtoken"],
    ext_modules=ext_modules,
    zip_safe=False,
)