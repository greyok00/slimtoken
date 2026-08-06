"""setup.py — slimtoken build. Cython compilation is MANDATORY.

slimtoken ships as NATIVE COMPILED CODE (.so/.pyd), not pure Python. This is
intentional: compilation both speeds up the hot path AND protects the source
(the readable .py is not what runs). There is NO pure-Python fallback — if
Cython or a C compiler is unavailable, the build fails with a clear message
rather than silently shipping unprotected, slow source.

Build modes:
  python setup.py build_ext --inplace   # .so next to .py (dev + tests vs .so)
  pip install .                          # installed compiled copy
  pip wheel .                             # .so-only wheel (no .py source shipped)
"""
import sys
from setuptools import setup, Extension
from setuptools.command.build_py import build_py

# ── Mandatory: Cython must be importable ──────────────────────────────────────
try:
    from Cython.Build import cythonize
except ImportError as e:
    raise SystemExit(
        "[slimtoken] Cython is required to build (slimtoken ships as compiled "
        "native code, not pure Python). Install it:  pip install Cython") from e

# Every module in the package is compiled to native code.
MODS = [
    "__init__", "pipeline", "tool_minify", "system_minify", "message_minify",
    "dedup_tool_results", "distill_old_turns", "token_budget", "context_prune",
    "upstream", "tls", "proxy", "config_optimizer", "cli", "lazy_mcp",
]

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


class _build_py(build_py):
    """Ship a .so-only wheel: don't copy slimtoken's .py sources into the wheel
    (the compiled .so from build_ext provides every module). This keeps readable
    source out of the distributed artifact."""

    def find_package_modules(self, package, package_dir_):
        mods = super().find_package_modules(package, package_dir_)
        if package == "slimtoken":
            return []  # compiled .so provides these — no .py in the wheel
        return mods


setup(
    package_dir={"": "src"},
    packages=["slimtoken"],
    ext_modules=ext_modules,
    cmdclass={"build_py": _build_py},
    # Ship ONLY the compiled .so — neither the readable .py source nor the
    # Cython-generated .c intermediates go into the wheel.
    exclude_package_data={"slimtoken": ["*.py", "*.c"]},
    zip_safe=False,
)