"""tokencount — real token counting with a bundled cl100k_base tokenizer.

Replaces the ``len(json)//4`` heuristic for budget decisions. The encoding
ships in ``slimtoken/data/cl100k_base.tiktoken`` so it works offline (no
first-use network fetch). Falls back to the ``len//4`` heuristic if tiktoken
is missing or the file is absent.

Why this module exists: the old ``estimate_tokens_obj`` did a full
``json.dumps(obj)`` of the ENTIRE body just to take ``len()//4`` — called 2x
unconditionally per request plus 1+N times inside ``enforce_budget`` (once per
candidate drop count). That hidden serialization tax dominated the pipeline.
Here, counting walks the structure and sums per-field counts that are memoized
by content hash, so stable system prompts / tool defs / repeated tool results
are counted once per session, and the budget search is O(1) per candidate via
prefix sums instead of a full re-serialize.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._deps import HAS_TIKTOKEN, jdumps, xhash

# ── encoder singleton ─────────────────────────────────────────────────────────
_ENCODER = None  # tiktoken.Encoding | None
_ENC_TRIED = False

# cl100k_base BPE pattern + special tokens (OpenAI public values).
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
    """Return a tiktoken.Encoding for cl100k_base from the bundled file, or None."""
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
        else:  # fallback: let tiktoken resolve/download it
            _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENCODER = None
    return _ENCODER


def _heuristic(text: str) -> int:
    return max(1, len(text) // 4)


# ── LRU cache ─────────────────────────────────────────────────────────────────
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
    """Token count of a string, cached by content hash."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    key = xhash(text.encode("utf-8", errors="replace"))
    enc = get_encoder()
    if enc is None:
        return _heuristic(text)
    return _cached(key, lambda: len(enc.encode(text)))


def count_bytes(data: bytes) -> int:
    """Token count of raw bytes (decoded as text); cached."""
    if not data:
        return 0
    key = xhash(data)
    enc = get_encoder()
    if enc is None:
        return max(1, len(data) // 4)
    return _cached(key, lambda: len(enc.encode(data.decode("utf-8", errors="replace"))))


# ── structural counts (no whole-body serialize) ───────────────────────────────
def count_content(content: Any) -> int:
    """Tokens for a message ``content`` field (str | list of blocks | other)."""
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
                # input is usually small; count its JSON repr cheaply.
                total += max(1, len(jdumps(block.get("input", {}))) // 4)
            elif t == "image":
                total += 8  # image block overhead, negligible
            else:
                total += max(1, len(jdumps(block)) // 4)
        return total
    # scalar / dict
    try:
        return max(1, len(jdumps(content)) // 4)
    except Exception:
        return max(1, len(str(content)) // 4)


def count_message(msg: Any) -> int:
    """Tokens for one message (content + a few for role/structural overhead)."""
    if not isinstance(msg, dict):
        return max(1, len(str(msg)) // 4)
    return count_content(msg.get("content")) + 4  # role + wrappers


def count_messages(messages: List) -> Tuple[int, List[int]]:
    """Return (total, per_message_counts)."""
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
    # Tools are stable across a session — cache on the whole list's hash.
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
        total += 8  # name + wrappers
    return total


def count_obj(body: dict) -> int:
    """Total token estimate for a parsed request body — NO whole-body serialize."""
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
    """prefix[i] = sum(per_msg[:i]); prefix[0] = 0. For O(1) drop-k math."""
    out = [0] * (len(per_msg) + 1)
    for i, v in enumerate(per_msg):
        out[i + 1] = out[i] + v
    return out


# ── heuristic-first gate (skip exact counting when far from budget) ───────────
def char_budget_threshold(budget: int) -> int:
    """A cheap char count above which exact counting is worth doing.
    ~4 chars/token heuristic upper bound — if total chars are well under
    budget*4, no point invoking the tokenizer."""
    return int(budget * 4 * 0.85)


def estimate_tokens_obj(obj) -> int:
    """Drop-in replacement for the old estimate_tokens_obj — no full serialize."""
    return count_obj(obj) if isinstance(obj, dict) else 0