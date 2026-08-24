
from __future__ import annotations

from typing import Dict

from .tokencount import (count_messages, count_system, count_tools,
                         message_prefix_sums)


def _has_tool_use(msg) -> bool:
    if not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use" for b in c)
    return False


def _has_tool_result(msg) -> bool:
    if not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return False


def _tool_pairs(msgs):

    open_uses = {}
    pairs = []
    for i, msg in enumerate(msgs):
        if not isinstance(msg, dict):
            continue
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("id") is not None:
                open_uses[b["id"]] = i
            elif b.get("type") == "tool_result":
                uid = b.get("tool_use_id")
                if uid in open_uses:
                    pairs.append((open_uses[uid], i))
                    del open_uses[uid]
    return pairs


def _valid_drop_points(msgs, k_max):

    pairs = _tool_pairs(msgs)

    forbidden = [(u + 1, r) for (u, r) in pairs]
    for k in range(0, k_max + 1):
        bad = any(lo <= k <= hi for (lo, hi) in forbidden)
        if not bad:
            yield k


def enforce_budget(body: dict, token_budget: int, keep_last: int = 8,
                   stats: Dict | None = None) -> dict:

    msgs = body.get("messages")
    if not isinstance(msgs, list) or len(msgs) <= keep_last:
        return body

    sys_tok = count_system(body.get("system"))
    tools_tok = count_tools(body.get("tools"))
    msg_total, per_msg = count_messages(msgs)
    prefix = message_prefix_sums(per_msg)
    total = sys_tok + tools_tok + msg_total
    if total <= token_budget:
        return body

    k_max = len(msgs) - keep_last
    valid = list(_valid_drop_points(msgs, k_max))
    if not valid:
        return body

    nb = dict(body)


    chosen = None
    for k in sorted(valid):
        if k == 0:
            continue
        if (total - prefix[k]) <= token_budget:
            chosen = k
            break
    if chosen is None:
        chosen = max(valid)
    if chosen <= 0:
        return body
    nb["messages"] = msgs[chosen:]
    if stats is not None:
        stats["budget_dropped"] = chosen
        stats["budget_tokens_before"] = total
        stats["budget_tokens_after"] = total - prefix[chosen]
    return nb