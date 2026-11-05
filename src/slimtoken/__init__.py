"""slimtoken: a token-optimization layer for LLM requests.

Exposes the request minification pipeline (MinifyConfig, MinifyStats,
minify_request) and wires in compiled fix modules when present.
"""
"""slimtoken: a token-optimization layer for LLM requests.

Exposes the request minification pipeline (MinifyConfig, MinifyStats,
minify_request) and wires in compiled fix modules when present.
"""
"""slimtoken: a token-optimization layer for LLM requests.

Exposes the request minification pipeline (MinifyConfig, MinifyStats,
minify_request) and wires in compiled fix modules when present.
"""
"""slimtoken: a token-optimization layer for LLM requests.

Exposes the request minification pipeline (MinifyConfig, MinifyStats,
minify_request) and wires in compiled fix modules when present.
"""
"""slimtoken: a token-optimization layer for LLM requests.

Exposes the request minification pipeline (MinifyConfig, MinifyStats,
minify_request) and wires in compiled fix modules when present.
"""
import sys as _sys
import types as _types
__version__ = '0.7.2'

def _wire_fixes() -> None:
    try:
        from . import _artifacts_fix as _af
        _af.apply_fix()
    except Exception:
        pass
    try:
        from . import _pipeline_fix as _pf
        _pf.apply_fix()
        _shim = _types.ModuleType('slimtoken.pipeline')
        _shim.__dict__.update(_pf.__dict__)
        _shim.__file__ = getattr(_pf, '__file__', None)
        _sys.modules['slimtoken.pipeline'] = _shim
        globals()['pipeline'] = _shim
    except Exception:
        try:
            from . import pipeline as _native_pipeline
            globals()['pipeline'] = _native_pipeline
        except Exception:
            pass
_wire_fixes()
from .pipeline import MinifyConfig, MinifyStats, minify_request
__all__ = ['MinifyConfig', 'MinifyStats', 'minify_request', 'prompt_reframe']
