"""Elision of old prose turns, coding-safe: nothing code-like is touched.

Elision only applies to plain prose, and only when the full original text is
first stored in the artifact store so it remains retrievable verbatim through
the digest published in the marker. Anything that looks like code (fenced,
inline, or indented code, embedded JSON objects) is never elided at all.
"""

from __future__ import annotations

import re
from typing import Dict, List

from .message_minify import split_fences

DEFAULT_MAX_CHARS = 4096

_MIN_DISTILL_LEN = 360

# Sentence boundary: ASCII stops plus CJK fullwidth period U+3002,
# exclamation U+FF01 and question U+FF1F marks.
_SENT_BOUNDARY = re.compile("[.!?\\u3002\\uff01\\uff1f]\\s")

_JSON_OBJECT_RE = re.compile(r"""(\{["']|["']\})""")
_INDENTED_CODE_RE = re.compile(r"\n[ \t]{4,}\S")

def _looks_like_code(text: str) -> bool:
    """True when the text carries inline code, indented code, or embedded JSON."""
    return "`" in text or _JSON_OBJECT_RE.search(text) is not None \
        or _INDENTED_CODE_RE.search(text) is not None

def _truncate_prose(seg: str, budget: int) -> str:
    """Cut ``seg`` to ``budget`` chars at the first safe sentence boundary."""
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

def _elide(text: str, max_chars: int) -> str:
    """Elide the middle of ``text`` down to ``max_chars`` with a recallable digest.

    The exact original is stored in the artifact store first; if that store
    cannot accept it the text is returned unchanged rather than dropped.
    """
    from . import artifacts

    digest = artifacts.store(text, kind="distill", origin="distill_old_turns")
    if digest is None:
        return text
    head = int(max_chars * 0.7)
    tail = max(0, max_chars - head)
    head_txt = _truncate_prose(text, head)
    tail_txt = text[len(text) - min(tail, len(text) - len(head_txt) - 60):]
    kept = len(head_txt) + len(tail_txt)
    marker = ("\n[slimtoken: %d chars elided - digest %s"
              " - recall: slimtoken recall %s]\n") % (len(text) - kept, digest, digest)
    return head_txt + marker + tail_txt

def distill_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Elide overlong plain prose; return code-like or short text untouched.

    Fenced blocks are kept verbatim; only prose between them is elided, and
    only when the original is artifact-backed via :func:`_elide`.
    """
    if not isinstance(text, str) or len(text) <= _MIN_DISTILL_LEN:
        return text
    if _looks_like_code(text):
        return text
    segs = split_fences(text)
    fences = any(is_fence for is_fence, _ in segs)
    prose_chars = sum(len(seg) for is_fence, seg in segs if not is_fence)
    if prose_chars <= max_chars:
        return text
    if not fences:
        return _elide(text, max_chars)
    kept = []
    prose_budget = max_chars
    for is_fence, seg in segs:
        if is_fence or not seg.strip():
            kept.append(seg)
            continue
        if len(seg) > prose_budget and len(seg) > _MIN_DISTILL_LEN:
            chunk = _elide(seg, prose_budget)
        else:
            chunk = seg
        kept.append(chunk)
        prose_budget -= len(chunk)
    return "".join(kept).strip()

def distill_old_turns(messages: List[Dict], stats: Dict,
                      keep_last: int = 8, max_chars: int = DEFAULT_MAX_CHARS,
                      include_user: bool = False) -> List[Dict]:
    """Distill assistant turns (optionally user turns) older than ``keep_last``.

    Returns the message list unchanged when no content was actually elided;
    ``stats`` gains a ``distill_count`` when it is a plain dict.
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
        if not include_user and msg.get("role") != "assistant":
            new_msgs.append(msg)
            continue
        c = msg.get("content")
        if isinstance(c, str):
            nc = distill_text(c, max_chars)
            if nc != c:
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
                    if nt != block["text"]:
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
