"""_deps — single import surface for the optional fast dependencies.

slimtoken ships with orjson, xxhash, and tiktoken (declared in pyproject
``dependencies``), but every one of them degrades gracefully to a stdlib
fallback if absent. Importing JSON / hashing / tokenizing through this module
keeps the rest of the code agnostic to which backend is active.

  jdumps(obj, sort_keys=False) -> bytes     # orjson -> json+encode
  jloads(data) -> obj                        # orjson -> json
  xhash(data: bytes) -> int                  # xxhash3_64 -> sha256 truncated
  HAS_ORJSON / HAS_XXHASH / HAS_TIKTOKEN     # capability flags
"""
from __future__ import annotations

import hashlib
import json

# ── JSON ─────────────────────────────────────────────────────────────────────
try:
    import orjson as _orjson
    HAS_ORJSON = True

    def jdumps(obj, sort_keys: bool = False) -> bytes:
        opt = _orjson.OPT_SORT_KEYS if sort_keys else 0
        return _orjson.dumps(obj, option=opt)
except ImportError:  # pragma: no cover - fallback when orjson not installed
    HAS_ORJSON = False

    def jdumps(obj, sort_keys: bool = False) -> bytes:
        return json.dumps(obj, sort_keys=sort_keys, ensure_ascii=False).encode()


def jloads(data):
    """Parse JSON from bytes or str."""
    if HAS_ORJSON:
        return _orjson.loads(data)
    if isinstance(data, (bytes, bytearray)):
        return json.loads(data.decode("utf-8"))
    return json.loads(data)


# ── hashing ───────────────────────────────────────────────────────────────────
try:
    import xxhash as _xxhash
    HAS_XXHASH = True

    def xhash(data: bytes) -> int:
        return _xxhash.xxh3_64_intdigest(data)
except ImportError:  # pragma: no cover
    HAS_XXHASH = False

    def xhash(data: bytes) -> int:
        return int.from_bytes(hashlib.sha256(data).digest()[:8], "little")


# ── tokenizer capability flag (loader lives in tokencount.py) ─────────────────
try:
    import tiktoken as _tiktoken  # noqa: F401
    HAS_TIKTOKEN = True
except ImportError:  # pragma: no cover
    HAS_TIKTOKEN = False