
from __future__ import annotations

import hashlib
import json


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

    if HAS_ORJSON:
        return _orjson.loads(data)
    if isinstance(data, (bytes, bytearray)):
        return json.loads(data.decode("utf-8"))
    return json.loads(data)



try:
    import xxhash as _xxhash
    HAS_XXHASH = True

    def xhash(data: bytes) -> int:
        return _xxhash.xxh3_64_intdigest(data)
except ImportError:  # pragma: no cover
    HAS_XXHASH = False

    def xhash(data: bytes) -> int:
        return int.from_bytes(hashlib.sha256(data).digest()[:8], "little")



try:
    import tiktoken as _tiktoken  # noqa: F401
    HAS_TIKTOKEN = True
except ImportError:  # pragma: no cover
    HAS_TIKTOKEN = False