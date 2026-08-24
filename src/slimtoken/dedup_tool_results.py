
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List



DEFAULT_MIN_CHARS = 200


def _content_key(content: Any) -> str:

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

    note = "[slimtoken: identical to a later tool_result; omitted %d chars]" % omitted
    if isinstance(original, list):
        return [{"type": "text", "text": note}]
    return note


def dedup_tool_results(messages: List[Dict], stats: Dict, min_chars: int = DEFAULT_MIN_CHARS) -> List[Dict]:

    if not isinstance(messages, list) or len(messages) < 2:
        return messages


    latest: Dict[str, int] = {}
    occurrences: List[tuple] = []
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
            latest[key] = mi
            occurrences.append((mi, bi, key, rc, n))


    from collections import Counter
    key_msg_counts = Counter()
    for mi, bi, key, rc, n in occurrences:
        key_msg_counts[key] += 1
    dup_keys = {k for k, cnt in key_msg_counts.items() if cnt > 1}
    if not dup_keys:
        return messages


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