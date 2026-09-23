
from __future__ import annotations

import re
from typing import Dict, List

from .message_minify import split_fences

DEFAULT_MAX_CHARS = 4096

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

    # 2026-09-23 loss-preserving rewrite (owner: "FIXED not disabled"): the
    # old version dropped every code fence after the first and hard-chopped
    # prose with no marker — that destroyed code and context in old turns
    # and contributed to agent loops. Now: EVERY code fence is kept
    # verbatim, prose keeps head AND tail with an explicit elision marker
    # naming the drop count, so a model reading a distilled turn knows
    # exactly what was elided and can still see the conclusion.
    if not isinstance(text, str) or len(text) <= _MIN_DISTILL_LEN:
        return text
    segs = split_fences(text)
    prose = [seg for is_fence, seg in segs if not is_fence and seg.strip()]
    fences_kept = sum(1 for is_fence, _ in segs if is_fence)
    prose_chars = sum(len(seg) for is_fence, seg in segs if not is_fence)
    if fences_kept == 0 and prose_chars <= max_chars:
        return text
    kept = []
    prose_budget = max_chars
    for is_fence, seg in segs:
        if is_fence:
            # ALL fences preserved verbatim — code is never touched
            kept.append(seg)
            continue
        if not seg.strip():
            kept.append(seg)
            continue
        if prose_budget <= 0 or len(seg) <= _MIN_DISTILL_LEN:
            if len(seg) > prose_budget:
                kept.append(f"\n[slimtoken: {len(seg.strip())} chars of prose elided]\n")
            else:
                kept.append(seg)
            continue
        head = int(prose_budget * 0.7)
        tail = max(0, prose_budget - head)
        if len(seg) > prose_budget:
            head_txt = _truncate_prose(seg, head)
            tail_txt = seg[len(seg) - min(tail // 2, len(seg) - len(head_txt) - 60):]
            chunk = head_txt + "\n[slimtoken: %d chars elided]\n" % (len(seg) - len(head_txt) - len(tail_txt)) + tail_txt
        else:
            chunk = seg
        kept.append(chunk)
        prose_budget -= len(chunk)
    result = "".join(kept).strip()
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