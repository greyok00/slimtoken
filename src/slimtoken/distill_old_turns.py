"""distill_old_turns — compress old prose into extractive summaries (prompt pruning).

Long coding-agent conversations carry verbose old turns — multi-paragraph
explanations, reasoning chains, restatements — that the model no longer needs
verbatim once a few turns have passed. This stage compresses the PROSE of
messages older than ``keep_last`` into a short extractive summary (first
sentence(s) + a note), while leaving the most recent ``keep_last`` messages
byte-identical and leaving every ``tool_use`` / ``tool_result`` / ``image``
block untouched.

No model call is made — distillation is a cheap, deterministic, fence-aware
extraction: keep the lead of each old text block, drop the tail, mark it.
This is the "prompt pruning" that yields large reductions on bloated
histories without the risk of a hard drop (nothing is removed, only
compressed).

PAIR-SAFE: it only rewrites the ``text`` field of text blocks in OLD
messages. It never removes a message, never reorders, and never touches
tool blocks — so tool_use ↔ tool_result pairing and ordering are preserved.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from .message_minify import split_fences

DEFAULT_MAX_CHARS = 240
# Only distill text blocks longer than this (short old turns are kept whole).
_MIN_DISTILL_LEN = 360

_SENT_BOUNDARY = re.compile(r"[.!?。！？]\s")


def _truncate_prose(seg: str, budget: int) -> str:
    """Keep up to ``budget`` chars of a prose segment, cutting at a sentence
    boundary when possible. Returns the truncated chunk (no note)."""
    if len(seg) <= budget:
        return seg
    chunk = seg[:budget]
    m = None
    for bm in _SENT_BOUNDARY.finditer(chunk):
        if bm.start() >= 40:  # keep at least one real sentence
            m = bm
            break
    if m is not None:
        return chunk[: m.start() + 1]
    return chunk.rstrip()


def distill_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Fence-aware extractive distillation of a single text string.

    Code fences are left verbatim (but only the FIRST fence is kept — later
    example blocks in an old message are dropped). Prose outside fences is
    truncated to the first ~``max_chars`` chars at a sentence boundary. A note
    is appended so the model knows content was compressed.
    """
    if not isinstance(text, str) or len(text) <= _MIN_DISTILL_LEN:
        return text
    segs = split_fences(text)
    kept = []
    fence_seen = 0
    prose_budget = max_chars
    for is_fence, seg in segs:
        if is_fence:
            fence_seen += 1
            if fence_seen > 1:
                continue  # drop later code blocks in old messages
            kept.append(seg)
        else:
            if prose_budget <= 0:
                continue
            chunk = _truncate_prose(seg, prose_budget)
            kept.append(chunk)
            prose_budget -= len(chunk)
    result = "".join(kept).strip()
    if len(result) < len(text):
        result += "\n\n[slimtoken: distilled from %d chars]" % len(text)
    return result


def distill_old_turns(messages: List[Dict], stats: Dict,
                      keep_last: int = 8, max_chars: int = DEFAULT_MAX_CHARS) -> List[Dict]:
    """Return messages with old prose distilled. Recent ``keep_last`` untouched.

    Never mutates input. Returns the original list object if nothing changed.
    """
    if not isinstance(messages, list) or len(messages) <= keep_last:
        return messages
    cutoff = len(messages) - keep_last
    new_msgs = []
    count = 0
    for i, msg in enumerate(messages):
        if i >= cutoff or not isinstance(msg, dict):
            new_msgs.append(msg)
            continue
        c = msg.get("content")
        if isinstance(c, str):
            nc = distill_text(c, max_chars)
            if nc is not c and nc != c:
                new_msgs.append({**msg, "content": nc})
                count += 1
            else:
                new_msgs.append(msg)
        elif isinstance(c, list):
            changed = False
            nc = []
            for block in c:
                if (isinstance(block, dict) and block.get("type") == "text"
                        and isinstance(block.get("text"), str)):
                    nt = distill_text(block["text"], max_chars)
                    if nt is not block["text"] and nt != block["text"]:
                        nb = dict(block)
                        nb["text"] = nt
                        nc.append(nb)
                        changed = True
                        continue
                nc.append(block)
            new_msgs.append({**msg, "content": nc} if changed else msg)
            if changed:
                count += 1
        else:
            new_msgs.append(msg)
    if count and stats is not None:
        stats["distill_count"] = count
    return new_msgs if count else messages