
from __future__ import annotations

import re
from typing import Dict, List

from .message_minify import split_fences

DEFAULT_MAX_CHARS = 240

_MIN_DISTILL_LEN = 360

_SENT_BOUNDARY = re.compile(r"[.!?。！？]\s")


def _truncate_prose(seg: str, budget: int) -> str:

    if len(seg) <= budget:
        return seg
    chunk = seg[:budget]
    m = None
    for bm in _SENT_BOUNDARY.finditer(chunk):
        if bm.start() >= 40:
            m = bm
            break
    if m is not None:
        return chunk[: m.start() + 1]
    return chunk.rstrip()


def distill_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:

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
                continue
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
                      keep_last: int = 8, max_chars: int = DEFAULT_MAX_CHARS,
                      include_user: bool = False) -> List[Dict]:

    if not isinstance(messages, list) or len(messages) <= keep_last:
        return messages
    cutoff = len(messages) - keep_last
    new_msgs = []
    count = 0
    for i, msg in enumerate(messages):
        if i >= cutoff or not isinstance(msg, dict):
            new_msgs.append(msg)
            continue
        if not include_user and msg.get("role") != "assistant":
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