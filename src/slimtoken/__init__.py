"""slimtoken package init (0.7.2).

0.7.2 audit wiring: two pure-Python repair shims are installed here, before any
compiled module gets a chance to import the submodules they replace.

* ``_artifacts_fix`` — wraps the compiled ``slimtoken.artifacts`` module so
  ``recall``/``get``/``get_bytes`` verify the sha256 digest and never return
  corrupted bytes, and stored blobs/files/dirs are created owner-only.
* ``_pipeline_fix`` — replaces ``slimtoken.pipeline`` in ``sys.modules`` with
  a delegate whose ``minify_request`` runs the shipped stages minus ``dedup``,
  then applies the append-only dedup policy (later copies stubbed, earlier
  messages byte-stable). Set ``SLIMTOKEN_DEDUP_APPEND_ONLY=0`` to restore the
  shipped oldest-copy-stubbing behavior.

Both shims fall back to the compiled originals if they fail to import, so a
broken repair can only ever degrade to pre-0.7.2 behavior, never to a crashed
import.
"""
import sys as _sys
import types as _types

__version__ = '0.7.2'


def _wire_fixes() -> None:
    # --- artifacts: integrity + permissions (patches the native module in place)
    try:
        from . import _artifacts_fix as _af
        _af.apply_fix()
    except Exception:
        pass

    # --- pipeline: append-only dedup (registered over the compiled submodule)
    try:
        from . import _pipeline_fix as _pf
        _pf.apply_fix()                      # loads the native .so into sys.modules
        _shim = _types.ModuleType("slimtoken.pipeline")
        _shim.__dict__.update(_pf.__dict__)
        _shim.__file__ = getattr(_pf, "__file__", None)
        _sys.modules["slimtoken.pipeline"] = _shim
        globals()["pipeline"] = _shim
    except Exception:
        try:
            from . import pipeline as _native_pipeline
            globals()["pipeline"] = _native_pipeline
        except Exception:
            pass


_wire_fixes()

from .pipeline import MinifyConfig, MinifyStats, minify_request  # noqa: E402

__all__ = ['MinifyConfig', 'MinifyStats', 'minify_request', 'prompt_reframe']