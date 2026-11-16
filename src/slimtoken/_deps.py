"""Optional fast paths: orjson-backed JSON codecs and a fast 64-bit hash.

Each helper falls back to a stdlib implementation when the acceleration
library is not installed; the matching HAS_* flag records which path is active.
"""
from __future__ import annotations
import hashlib
import json
try:
    import orjson as _orjson
    HAS_ORJSON = True

    def jdumps(obj, sort_keys: bool=False) -> bytes:
        """Serialize to compact JSON bytes, optionally sorted by key."""
        opt = _orjson.OPT_SORT_KEYS if sort_keys else 0
        return _orjson.dumps(obj, option=opt)
except ImportError:
    HAS_ORJSON = False

    def jdumps(obj, sort_keys: bool=False) -> bytes:
        """Serialize to compact JSON bytes, optionally sorted by key."""
        return json.dumps(obj, sort_keys=sort_keys, ensure_ascii=False).encode()

def jloads(data):
    """Parse JSON from bytes or decoded text."""
    if HAS_ORJSON:
        return _orjson.loads(data)
    if isinstance(data, (bytes, bytearray)):
        return json.loads(data.decode('utf-8'))
    return json.loads(data)
try:
    import xxhash as _xxhash
    HAS_XXHASH = True

    def xhash(data: bytes) -> int:
        """Return a 64-bit hash of the data as an int."""
        return _xxhash.xxh3_64_intdigest(data)
except ImportError:
    HAS_XXHASH = False

    def xhash(data: bytes) -> int:
        """Return a 64-bit hash of the data as an int."""
        return int.from_bytes(hashlib.sha256(data).digest()[:8], 'little')
from importlib.util import find_spec as _find_spec

HAS_TIKTOKEN = _find_spec("tiktoken") is not None
