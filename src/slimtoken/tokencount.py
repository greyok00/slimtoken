
from __future__ import annotations

import base64
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ._deps import HAS_TIKTOKEN, jdumps, xhash


_ENCODER = None
_ENC_TRIED = False


_CL100K_PAT = (
    r"""(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}|"""
    r""" ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"""
)
_CL100K_SPECIAL = {
    "<|endoftext|>": 100257,
    "<|fim_prefix|>": 100258,
    "<|fim_middle|>": 100259,
    "<|fim_suffix|>": 100260,
    "<|endofprompt|>": 100276,
}
_BUNDLED = Path(__file__).parent / "data" / "cl100k_base.tiktoken"


def _load_mergeable_ranks(path: Path) -> Dict[bytes, int]:
    ranks: Dict[bytes, int] = {}
    with open(path, "rb") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tok_b64, rank = line.split()
            ranks[base64.b64decode(tok_b64)] = int(rank)
    return ranks


def get_encoder():

    global _ENCODER, _ENC_TRIED
    if _ENC_TRIED:
        return _ENCODER
    _ENC_TRIED = True
    if not HAS_TIKTOKEN:
        return None
    try:
        import tiktoken
        if _BUNDLED.exists():
            ranks = _load_mergeable_ranks(_BUNDLED)
            _ENCODER = tiktoken.Encoding(
                "cl100k_base", pat_str=_CL100K_PAT,
                mergeable_ranks=ranks, special_tokens=_CL100K_SPECIAL)
        else:
            _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENCODER = None
    return _ENCODER


def _heuristic(text: str) -> int:
    return max(1, len(text) // 4)



_CACHE: "OrderedDict[int, int]" = OrderedDict()
_CACHE_CAP = 8192


def _cached(key: int, fn):
    v = _CACHE.get(key)
    if v is not None:
        _CACHE.move_to_end(key)
        return v
    v = fn()
    _CACHE[key] = v
    _CACHE.move_to_end(key)
    if len(_CACHE) > _CACHE_CAP:
        _CACHE.popitem(last=False)
    return v


def count(text: str) -> int:

    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    key = xhash(text.encode("utf-8", errors="replace"))
    enc = get_encoder()
    if enc is None:
        return _heuristic(text)
    # disallow_special=False: upstream model output (e.g. GLM's literal
    # </think>) regularly lands in request history. Raising here killed the whole
    # request ("handle error" -> connection closed -> client timeout retry loop).
    # Count special tokens as ordinary tokens instead — never raise.
    return _cached(key, lambda: len(enc.encode(text, disallowed_special=())))


def count_bytes(data: bytes) -> int:

    if not data:
        return 0
    key = xhash(data)
    enc = get_encoder()
    if enc is None:
        return max(1, len(data) // 4)
    return _cached(key, lambda: len(enc.encode(
        data.decode("utf-8", errors="replace"), disallowed_special=())))



def count_content(content: Any) -> int:

    if isinstance(content, str):
        return count(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if not isinstance(block, dict):
                total += max(1, len(str(block)) // 4)
                continue
            t = block.get("type")
            if t == "text" and isinstance(block.get("text"), str):
                total += count(block["text"])
            elif t == "tool_result":
                total += count_content(block.get("content"))
            elif t == "tool_use":

                total += max(1, len(jdumps(block.get("input", {}))) // 4)
            elif t == "image":
                total += 8
            else:
                total += max(1, len(jdumps(block)) // 4)
        return total

    try:
        return max(1, len(jdumps(content)) // 4)
    except Exception:
        return max(1, len(str(content)) // 4)


def count_message(msg: Any) -> int:

    if not isinstance(msg, dict):
        return max(1, len(str(msg)) // 4)
    return count_content(msg.get("content")) + 4


def count_messages(messages: List) -> Tuple[int, List[int]]:

    if not isinstance(messages, list):
        return 0, []
    per = [count_message(m) for m in messages]
    return sum(per), per


def count_system(system: Any) -> int:
    if system is None:
        return 0
    if isinstance(system, str):
        return count(system)
    if isinstance(system, list):
        total = 0
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text" \
                    and isinstance(block.get("text"), str):
                total += count(block["text"])
            elif isinstance(block, str):
                total += count(block)
            else:
                total += max(1, len(str(block)) // 4)
        return total
    return max(1, len(str(system)) // 4)


def count_tools(tools: Any) -> int:
    if not isinstance(tools, list) or not tools:
        return 0

    try:
        key = xhash(jdumps(tools, sort_keys=True))
    except Exception:
        key = xhash(repr(tools).encode())
    return _cached(key, lambda: _count_tools_uncached(tools))


def _count_tools_uncached(tools: list) -> int:
    total = 0
    for t in tools:
        if not isinstance(t, dict):
            total += max(1, len(str(t)) // 4)
            continue
        if isinstance(t.get("description"), str):
            total += count(t["description"])
        if "input_schema" in t:
            total += max(1, len(jdumps(t["input_schema"])) // 4)
        total += 8
    return total


def count_obj(body: dict) -> int:

    if not isinstance(body, dict):
        return 0
    total = 0
    if "system" in body:
        total += count_system(body["system"])
    if "tools" in body:
        total += count_tools(body["tools"])
    if "messages" in body:
        t, _ = count_messages(body["messages"])
        total += t
    return total


def message_prefix_sums(per_msg: List[int]) -> List[int]:

    out = [0] * (len(per_msg) + 1)
    for i, v in enumerate(per_msg):
        out[i + 1] = out[i] + v
    return out



def char_budget_threshold(budget: int) -> int:

    return int(budget * 4 * 0.85)


def estimate_tokens_obj(obj) -> int:

    return count_obj(obj) if isinstance(obj, dict) else 0