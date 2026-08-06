"""dedup_tool_results — collapse repeated tool_result contents.

A coding agent re-reads the same files over and over. Each re-read ships the
FULL file contents back to the model as a ``tool_result``. When the same
content appears more than once in a conversation, every copy after the first
is pure token waste: the model already has that text available in a later
(= more relevant) result.

This stage finds ``tool_result`` blocks whose content is byte-identical to a
LATER tool_result, and replaces the older copy's content with a short stub
referencing the duplicate. The LATEST occurrence is always kept verbatim
(its content is the most relevant). Net effect: the information is preserved
(available in the newer result) while the older copies stop costing tokens.

PAIR-SAFE by construction: it only rewrites the ``content`` field of
``tool_result`` blocks. It never removes messages, never reorders them, and
never touches ``tool_use`` blocks or their ids — so every tool_result still
has its matching tool_use in the same position. The Anthropic API contract
(tool_use ↔ tool_result pairing + ordering) is preserved.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

# Only stub results at least this large — tiny results aren't worth the stub
# overhead and risk removing a short but unique value.
DEFAULT_MIN_CHARS = 200


def _content_key(content: Any) -> str:
    """Stable hash key for a tool_result's content (str OR list of blocks)."""
    try:
        raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
    except Exception:
        raw = str(content)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _content_len(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(b.get("text", "")) if isinstance(b, dict) and isinstance(b.get("text"), str)
            else len(json.dumps(b, ensure_ascii=False)) if isinstance(b, dict)
            else len(str(b))
            for b in content
        )
    return len(str(content))


def _stub_content(original: Any, omitted: int) -> Any:
    """Build a stub that preserves the original content's SHAPE (str vs list)."""
    note = "[slimtoken: identical to a later tool_result; omitted %d chars]" % omitted
    if isinstance(original, list):
        return [{"type": "text", "text": note}]
    return note


def dedup_tool_results(messages: List[Dict], stats: Dict, min_chars: int = DEFAULT_MIN_CHARS) -> List[Dict]:
    """Return messages with older duplicate tool_result contents stubbed.

    Never mutates the input list or its dicts. Returns the original list object
    if nothing changed (so callers can detect a no-op by identity).
    """
    if not isinstance(messages, list) or len(messages) < 2:
        return messages

    # First pass: find the LATEST index for each content key (only large ones).
    latest: Dict[str, int] = {}
    occurrences: List[tuple] = []  # (msg_idx, block_idx, key, content, omitted)
    for mi, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        for bi, block in enumerate(c):
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            rc = block.get("content")
            n = _content_len(rc)
            if n < min_chars:
                continue
            key = _content_key(rc)
            latest[key] = mi  # overwrites → ends as the LAST msg index for this key
            occurrences.append((mi, bi, key, rc, n))

    # Which keys are actually duplicated (appear in >1 message)?
    from collections import Counter
    key_msg_counts = Counter()
    for mi, bi, key, rc, n in occurrences:
        key_msg_counts[key] += 1
    dup_keys = {k for k, cnt in key_msg_counts.items() if cnt > 1}
    if not dup_keys:
        return messages

    # Older occurrences (mi < latest[key]) of a dup key get stubbed.
    stubs: Dict[tuple, Any] = {}
    count = 0
    for mi, bi, key, rc, n in occurrences:
        if key not in dup_keys:
            continue
        if mi < latest[key]:
            stubs[(mi, bi)] = _stub_content(rc, n)
            count += 1
    if not stubs:
        return messages

    # Second pass: rebuild messages, applying stubs. Only changed messages get
    # a new dict; untouched messages keep their original object.
    new_msgs = []
    for mi, msg in enumerate(messages):
        if not isinstance(msg, dict):
            new_msgs.append(msg)
            continue
        c = msg.get("content")
        if not isinstance(c, list):
            new_msgs.append(msg)
            continue
        changed = False
        nc = []
        for bi, block in enumerate(c):
            if (mi, bi) in stubs and isinstance(block, dict):
                nb = dict(block)
                nb["content"] = stubs[(mi, bi)]
                nc.append(nb)
                changed = True
            else:
                nc.append(block)
        new_msgs.append({**msg, "content": nc} if changed else msg)
    if stats is not None:
        stats["dedup_count"] = count
    return new_msgs