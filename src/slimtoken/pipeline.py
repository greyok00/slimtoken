"""pipeline — the minification orchestrator (the slimtoken core).

``minify_request(body, cfg)`` takes a plain ``dict`` (a parsed Anthropic-style
request body) and a :class:`MinifyConfig`, and returns ``(new_body, MinifyStats)``.
It imports only the stdlib minify modules — nothing from any other project — so
the pipeline is fully standalone.

Every stage is ON by default. A stage never aborts the others: a failure is
recorded in ``stats.errors`` and the body passes through that stage untouched.

Counting uses :mod:`slimtoken.tokencount` (real cl100k_base tokenizer, cached by
content hash) — no full-body ``json.dumps`` just to count tokens. The message
stages (minify + dedup + distill) run in a single merged 2-pass
:func:`optimize_messages` (one analysis pass for the dedup map + per-message
counts, one transform pass) instead of 3-4 separate walks. Conditional skips
short-circuit stages that have nothing to do (no large tool_results → no
dedup; short conversation → no distill/budget; short system → no system minify).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Set

from .tool_minify import minify_tools
from .system_minify import minify_system
from .message_minify import minify_message_content
from .dedup_tool_results import (_content_key, _content_len, _stub_content,
                                  DEFAULT_MIN_CHARS as _DEDUP_MIN)
from .distill_old_turns import distill_text, DEFAULT_MAX_CHARS as _DISTILL_MAX
from .token_budget import enforce_budget
from .tokencount import count_obj
from collections import Counter


@dataclass
class MinifyConfig:
    token_budget: int = 131072      # 0 = off; default = generous backstop (only catches bloat)
    enabled_stages: Set[str] = field(default_factory=lambda: {
        "tools", "system", "messages", "dedup", "distill",
    })
    tool_skip: Set[str] = field(default_factory=set)
    keep_last: int = 8              # distill + budget: always keep most recent N
    dedup_min_chars: int = _DEDUP_MIN
    distill_max_chars: int = _DISTILL_MAX
    # Lossy opt-in (off by default — see tool_result_compress; not wired here).
    tool_compress: bool = False


@dataclass
class MinifyStats:
    tokens_in: int = 0
    tokens_out: int = 0
    tools_minified: int = 0
    system_minified: bool = False
    messages_minified: int = 0
    dedup_count: int = 0
    distill_count: int = 0
    budget_dropped: int = 0
    budget_tokens_before: int = 0
    budget_tokens_after: int = 0
    tool_compressed: int = 0
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        d = self.tokens_in - self.tokens_out
        pct = (d / self.tokens_in * 100) if self.tokens_in else 0.0
        s = (f"in={self.tokens_in} out={self.tokens_out} -{d}({pct:.0f}%) "
             f"tools={self.tools_minified} sys={'Y' if self.system_minified else 'N'} "
             f"msgs={self.messages_minified} dedup={self.dedup_count} "
             f"distill={self.distill_count} dropped={self.budget_dropped}"
              + (f" compressed={self.tool_compressed}" if self.tool_compressed else ""))
        if self.errors:
            s += f" ERRORS={len(self.errors)}"
        return s


def _minify_system_field(system):
    """System can be a string OR a list of text blocks. Returns minified copy."""
    if isinstance(system, str):
        return minify_system(system)
    if isinstance(system, list):
        out = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                nb = dict(block)
                nb["text"] = minify_system(block["text"])
                out.append(nb)
            else:
                out.append(block)
        return out
    return system


def _distill_content(content, max_chars):
    """Per-message distill (the inner logic of distill_old_turns). Returns
    (new_content, changed). Only text blocks are touched."""
    if isinstance(content, str):
        nc = distill_text(content, max_chars)
        return (nc, nc is not content and nc != content)
    if isinstance(content, list):
        changed = False
        nc = []
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)):
                nt = distill_text(block["text"], max_chars)
                if nt is not block["text"] and nt != block["text"]:
                    nc.append({**block, "text": nt})
                    changed = True
                    continue
            nc.append(block)
        return (nc if changed else content, changed)
    return (content, False)


def optimize_messages(messages, cfg: MinifyConfig, stats: MinifyStats):
    """Merged 2-pass message transform: minify + distill + dedup-stub.

    Pass 1 (analysis): build the dedup latest-map + dup stubs + per-message
    counts (one walk). Pass 2 (transform): per message, minify text blocks,
    distill old text, stub older-duplicate tool_results — one walk. Pair-safe
    by construction (only rewrites content fields; never removes/reorders).

    Returns the original list object if nothing changed (zero-copy no-op).
    """
    if not isinstance(messages, list) or len(messages) < 2:
        return messages
    n = len(messages)
    minify_on = "messages" in cfg.enabled_stages
    dedup_on = "dedup" in cfg.enabled_stages
    distill_on = "distill" in cfg.enabled_stages
    cutoff = (n - cfg.keep_last) if (distill_on and n > cfg.keep_last) else -1

    # ---- pass 1: analysis (dedup map + stubs) ----
    stubs: Dict[tuple, object] = {}
    if dedup_on:
        latest: Dict[str, int] = {}
        occurrences = []
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
                nlen = _content_len(rc)
                if nlen < cfg.dedup_min_chars:
                    continue
                key = _content_key(rc)
                latest[key] = mi
                occurrences.append((mi, bi, key, rc, nlen))
        key_counts = Counter()
        for _mi, _bi, key, _rc, _nlen in occurrences:
            key_counts[key] += 1
        dup_keys = {k for k, c in key_counts.items() if c > 1}
        for mi, bi, key, rc, nlen in occurrences:
            if key in dup_keys and mi < latest.get(key, mi):
                stubs[(mi, bi)] = _stub_content(rc, nlen)
        if stubs and stats is not None:
            stats.dedup_count = len(stubs)

    # ---- pass 2: transform ----
    new_msgs = []
    any_changed = False
    minify_hits = 0
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            new_msgs.append(msg)
            continue
        content = msg.get("content")
        local_changed = False
        minify_hit = False

        # minify text blocks (stage 3)
        if minify_on:
            nc = minify_message_content(content)
            if nc is not content:
                content = nc
                local_changed = True
                minify_hit = True

        # distill old text (stage 5) — operates on (possibly minified) content
        if distill_on and 0 <= i < cutoff:
            nc, changed = _distill_content(content, cfg.distill_max_chars)
            if changed:
                content = nc
                local_changed = True
                if stats is not None:
                    stats.distill_count += 1

        # dedup-stub older duplicate tool_results (stage 4)
        if stubs and isinstance(content, list):
            nc = []
            c2 = False
            for bi, block in enumerate(content):
                if (i, bi) in stubs and isinstance(block, dict):
                    nb = dict(block)
                    nb["content"] = stubs[(i, bi)]
                    nc.append(nb)
                    c2 = True
                else:
                    nc.append(block)
            if c2:
                content = nc
                local_changed = True

        if local_changed:
            new_msgs.append({**msg, "content": content})
            any_changed = True
        else:
            new_msgs.append(msg)
        if minify_hit:
            minify_hits += 1

    if stats is not None:
        stats.messages_minified = minify_hits
    return new_msgs if any_changed else messages


def minify_request(body: dict, cfg: MinifyConfig) -> tuple:
    """Minify a parsed request body. Returns (new_body, MinifyStats).

    Never mutates the input. Never raises from a stage — failures are recorded
    in stats so one broken stage can't break inference.
    """
    stats = MinifyStats()
    if not isinstance(body, dict):
        return body, stats
    stats.tokens_in = count_obj(body) if body else 0
    nb = dict(body)

    # 1. tools
    if "tools" in nb and "tools" in cfg.enabled_stages:
        try:
            before = len(nb.get("tools", []))
            nb["tools"] = minify_tools(nb.get("tools"), cfg.tool_skip)
            stats.tools_minified = before
        except Exception as e:
            stats.errors.append(f"tools:{e}")

    # 2. system — always run when enabled (blank-line bloat happens on short
    # prompts too; the pass is a cheap single fence-aware walk).
    if "system" in nb and "system" in cfg.enabled_stages:
        try:
            nb["system"] = _minify_system_field(nb["system"])
            stats.system_minified = True
        except Exception as e:
            stats.errors.append(f"system:{e}")

    # 3-5. messages: merged minify + distill + dedup in one 2-pass — conditional
    if "messages" in nb and any(s in cfg.enabled_stages for s in ("messages", "dedup", "distill")):
        try:
            msgs = nb.get("messages")
            if isinstance(msgs, list) and msgs:
                # short-circuit: nothing to distill/dedup on tiny conversations
                # (minify still runs on any size; it's cheap and per-message).
                new_msgs = optimize_messages(msgs, cfg, stats)
                if new_msgs is not msgs:
                    nb["messages"] = new_msgs
        except Exception as e:
            stats.errors.append(f"messages:{e}")

    # 6. budget — pair-safe hard prune (backstop). Conditional: only if over.
    if cfg.token_budget > 0 and "messages" in nb:
        try:
            nb = enforce_budget(nb, cfg.token_budget, keep_last=cfg.keep_last, stats=stats.__dict__)
        except Exception as e:
            stats.errors.append(f"budget:{e}")

    # 7. type-specific tool_result compression (lossy, opt-in). Pair-safe:
    #    only rewrites the `content` field of tool_result blocks.
    if cfg.tool_compress and "messages" in nb:
        try:
            from .tool_result_compress import compress_messages
            nb["messages"], n = compress_messages(nb.get("messages"))
            if n and stats is not None:
                stats.tool_compressed = n
        except Exception as e:
            stats.errors.append(f"tool_compress:{e}")

    stats.tokens_out = count_obj(nb) if nb else 0
    return nb, stats


# ── Chunked path helper ──────────────────────────────────────────────────────
def minify_chunked_first_event(event_bytes: bytes, cfg: MinifyConfig):
    """Minify the first SSE ``data: {...}`` event of a streaming request.

    Returns (new_event_bytes, stats). On ANY parse problem returns the input
    unchanged with empty stats (caller falls back to raw passthrough). Most
    tool/system mass lands in the first event of a streaming /v1/messages POST.
    """
    from ._deps import jloads, jdumps
    if not event_bytes:
        return event_bytes, MinifyStats()
    try:
        text = event_bytes.decode("utf-8")
    except Exception:
        return event_bytes, MinifyStats()
    idx = text.find("data: ")
    if idx < 0:
        return event_bytes, MinifyStats()
    nl = text.find("\n", idx)
    if nl < 0:
        return event_bytes, MinifyStats()
    payload = text[idx + 6:nl].strip()
    try:
        obj = jloads(payload)
    except Exception:
        return event_bytes, MinifyStats()
    if not isinstance(obj, dict):
        return event_bytes, MinifyStats()
    new_obj, stats = minify_request(obj, cfg)
    new_payload = jdumps(new_obj).decode("utf-8")
    new_event = (text[:idx + 6] + new_payload + text[nl:]).encode("utf-8")
    return new_event, stats