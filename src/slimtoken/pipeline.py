
from __future__ import annotations

import re
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



_DOM_THRESHOLD = 4096
_HTML_HINT = re.compile(r"<(?:html|!doctype|body|div|script)\b", re.IGNORECASE)


@dataclass
class MinifyConfig:
    token_budget: int = 131072
    enabled_stages: Set[str] = field(default_factory=lambda: {
        "tools", "system", "messages", "dedup", "distill",
    })
    tool_skip: Set[str] = field(default_factory=set)
    keep_last: int = 8
    dedup_min_chars: int = _DEDUP_MIN
    distill_max_chars: int = _DISTILL_MAX


    distill_include_user: bool = False

    tool_compress: bool = False

    minify_dom: bool = False


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
    dom_minified: int = 0
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


def _maybe_prune_dom_in_messages(messages, stats):

    if not isinstance(messages, list):
        return messages
    try:
        from .dom_pruner import prune_dom
    except Exception:
        return messages
    changed = False
    new_msgs = []
    for msg in messages:
        if not isinstance(msg, dict):
            new_msgs.append(msg)
            continue
        c = msg.get("content")
        if isinstance(c, list):
            nc = []
            for block in c:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    rc = block.get("content")
                    if isinstance(rc, str) and len(rc) > _DOM_THRESHOLD and _HTML_HINT.search(rc):
                        try:
                            pruned = prune_dom(rc, "proxy")
                            nb = dict(block)
                            nb["content"] = pruned
                            nc.append(nb)
                            stats.dom_minified += 1
                            changed = True
                            continue
                        except Exception as e:
                            stats.errors.append(f"dom:{e}")
                nc.append(block)
            new_msgs.append({**msg, "content": nc})
        else:
            new_msgs.append(msg)
    return new_msgs if changed else messages


def _distill_content(content, max_chars):

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

    if not isinstance(messages, list) or len(messages) < 2:
        return messages
    n = len(messages)
    minify_on = "messages" in cfg.enabled_stages
    dedup_on = "dedup" in cfg.enabled_stages
    distill_on = "distill" in cfg.enabled_stages
    cutoff = (n - cfg.keep_last) if (distill_on and n > cfg.keep_last) else -1


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


        if minify_on:
            nc = minify_message_content(content)
            if nc is not content:
                content = nc
                local_changed = True
                minify_hit = True




        if distill_on and 0 <= i < cutoff and (
                cfg.distill_include_user or msg.get("role") == "assistant"):
            nc, changed = _distill_content(content, cfg.distill_max_chars)
            if changed:
                content = nc
                local_changed = True
                if stats is not None:
                    stats.distill_count += 1


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

    stats = MinifyStats()
    if not isinstance(body, dict):
        return body, stats
    stats.tokens_in = count_obj(body) if body else 0
    nb = dict(body)


    if "tools" in nb and "tools" in cfg.enabled_stages:
        try:
            before = len(nb.get("tools", []))
            nb["tools"] = minify_tools(nb.get("tools"), cfg.tool_skip)
            stats.tools_minified = before
        except Exception as e:
            stats.errors.append(f"tools:{e}")



    if "system" in nb and "system" in cfg.enabled_stages:
        try:
            nb["system"] = _minify_system_field(nb["system"])
            stats.system_minified = True
        except Exception as e:
            stats.errors.append(f"system:{e}")


    if "messages" in nb and any(s in cfg.enabled_stages for s in ("messages", "dedup", "distill")):
        try:
            msgs = nb.get("messages")
            if isinstance(msgs, list) and msgs:


                new_msgs = optimize_messages(msgs, cfg, stats)
                if new_msgs is not msgs:
                    nb["messages"] = new_msgs
        except Exception as e:
            stats.errors.append(f"messages:{e}")


    if cfg.minify_dom and "messages" in nb:
        try:
            nb["messages"] = _maybe_prune_dom_in_messages(nb.get("messages"), stats)
        except Exception as e:
            stats.errors.append(f"dom:{e}")


    if cfg.token_budget > 0 and "messages" in nb:
        try:
            nb = enforce_budget(nb, cfg.token_budget, keep_last=cfg.keep_last, stats=stats.__dict__)
        except Exception as e:
            stats.errors.append(f"budget:{e}")



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



def minify_chunked_first_event(event_bytes: bytes, cfg: MinifyConfig):

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