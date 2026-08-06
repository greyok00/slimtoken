"""token_budget — estimate prompt size and enforce a soft token budget.

Conservative by design: the Anthropic Messages API REQUIRES every tool_use to
have a matching tool_result later in the conversation, and message ordering is
strict. So budget enforcement drops a CONTIGUOUS LEADING prefix of messages
that never splits a tool_use/tool_result pair, and always preserves the most
recent ``keep_last`` messages. If the leading messages are themselves tool
exchanges we can't safely drop, we drop nothing and just report over-budget.
"""
from __future__ import annotations

import json
from typing import Dict, List

from .context_prune import estimate_tokens


def estimate_tokens_obj(obj) -> int:
    """Estimate tokens for any JSON-serializable object."""
    try:
        return estimate_tokens(json.dumps(obj))
    except Exception:
        return 0


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
    """Return list of (use_idx, res_idx) for each tool_use→tool_result pair.

    A tool_use lives in an assistant message; its tool_result lives in a later
    user message referencing the same id. Pairs let us pick drop boundaries
    that never split a use from its result (which the API rejects).
    """
    open_uses = {}  # id -> use_idx
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
    """Yield valid leading-prefix drop counts in [0, k_max].

    A drop count k is valid iff no tool_use/tool_result pair straddles the
    boundary: i.e. for every pair (u, r), NOT (u < k <= r). k=0 is always valid.
    """
    pairs = _tool_pairs(msgs)
    # forbidden intervals (u, r] — k inside (u, r] would split the pair.
    forbidden = [(u + 1, r) for (u, r) in pairs]
    for k in range(0, k_max + 1):
        bad = any(lo <= k <= hi for (lo, hi) in forbidden)
        if not bad:
            yield k


def enforce_budget(body: dict, token_budget: int, keep_last: int = 8,
                   stats: Dict | None = None) -> dict:
    """Drop oldest messages to fit under ``token_budget``. Returns new body.

    ``system`` and ``tools`` are never dropped — they're cheap to keep relative
    to a long conversation and are required for every request. Only the
    ``messages`` array is trimmed, and only a safe leading prefix whose
    boundary never splits a tool_use from its tool_result (the API rejects
    orphaned results). If no safe drop gets under budget, we drop the largest
    safe prefix as best-effort and report remaining overage.
    """
    msgs = body.get("messages")
    if not isinstance(msgs, list) or len(msgs) <= keep_last:
        return body
    total = estimate_tokens_obj(body)
    if total <= token_budget:
        return body

    msgs = list(msgs)
    k_max = len(msgs) - keep_last
    valid = list(_valid_drop_points(msgs, k_max))
    if not valid:
        return body

    nb = dict(body)
    # Prefer the SMALLEST safe drop that gets under budget (preserve context).
    # If none gets under budget, use the LARGEST safe drop (best-effort).
    chosen = None
    for k in sorted(valid):
        if k == 0:
            continue
        nb["messages"] = msgs[k:]
        if estimate_tokens_obj(nb) <= token_budget:
            chosen = k
            break
    if chosen is None:
        chosen = max(valid)
        nb["messages"] = msgs[chosen:]
    if chosen <= 0:
        return body
    new_total = estimate_tokens_obj(nb)
    if stats is not None:
        stats["budget_dropped"] = chosen
        stats["budget_tokens_before"] = total
        stats["budget_tokens_after"] = new_total
    return nb