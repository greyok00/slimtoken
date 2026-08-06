"""pipeline — the minification orchestrator (the slimtoken core).

``minify_request(body, cfg)`` takes a plain ``dict`` (a parsed Anthropic-style
request body) and a :class:`MinifyConfig`, and returns ``(new_body, MinifyStats)``.
It imports only the stdlib minify modules — nothing from any other project — so
the pipeline is fully standalone.

Every stage is ON by default. A stage never aborts the others: a failure is
recorded in ``stats.errors`` and the body passes through that stage untouched.

Order (each stage independent):
  1. tools    — balanced tool-def minify
  2. system   — fence-aware system-prompt minify
  3. messages — code-aware text-block minify (tool I/O untouched); overhead-cut
  4. dedup    — collapse repeated tool_result contents (pair-safe)
  5. distill  — compress old prose turns into extractive summaries (prompt pruning)
  6. budget   — pair-safe hard history prune to a token budget (last; backstop)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Set

from .tool_minify import minify_tools
from .system_minify import minify_system
from .message_minify import minify_message_content
from .dedup_tool_results import dedup_tool_results, DEFAULT_MIN_CHARS as _DEDUP_MIN
from .distill_old_turns import distill_old_turns, DEFAULT_MAX_CHARS as _DISTILL_MAX
from .token_budget import enforce_budget, estimate_tokens_obj


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
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        d = self.tokens_in - self.tokens_out
        pct = (d / self.tokens_in * 100) if self.tokens_in else 0.0
        s = (f"in={self.tokens_in} out={self.tokens_out} -{d}({pct:.0f}%) "
             f"tools={self.tools_minified} sys={'Y' if self.system_minified else 'N'} "
             f"msgs={self.messages_minified} dedup={self.dedup_count} "
             f"distill={self.distill_count} dropped={self.budget_dropped}")
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


def minify_request(body: dict, cfg: MinifyConfig) -> tuple:
    """Minify a parsed request body. Returns (new_body, MinifyStats).

    Never mutates the input. Never raises from a stage — failures are recorded
    in stats so one broken stage can't break inference.
    """
    stats = MinifyStats()
    if not isinstance(body, dict):
        return body, stats
    stats.tokens_in = estimate_tokens_obj(body)
    nb = dict(body)

    # 1. tools
    if "tools" in nb and "tools" in cfg.enabled_stages:
        try:
            before = len(nb.get("tools", []))
            nb["tools"] = minify_tools(nb.get("tools"), cfg.tool_skip)
            stats.tools_minified = before
        except Exception as e:
            stats.errors.append(f"tools:{e}")

    # 2. system
    if "system" in nb and "system" in cfg.enabled_stages:
        try:
            nb["system"] = _minify_system_field(nb["system"])
            stats.system_minified = True
        except Exception as e:
            stats.errors.append(f"system:{e}")

    # 3. messages — overhead-cut: identity-based change detection (no json.dumps)
    if "messages" in nb and "messages" in cfg.enabled_stages:
        try:
            msgs = nb.get("messages")
            if isinstance(msgs, list):
                nm = []
                count = 0
                for msg in msgs:
                    if isinstance(msg, dict) and "content" in msg:
                        before = msg["content"]
                        after = minify_message_content(before)
                        if after is not before:
                            count += 1
                            nm.append({**msg, "content": after})
                        else:
                            nm.append(msg)
                    else:
                        nm.append(msg)
                nb["messages"] = nm
                stats.messages_minified = count
        except Exception as e:
            stats.errors.append(f"messages:{e}")

    # 4. dedup — collapse repeated tool_result contents (pair-safe)
    if "messages" in nb and "dedup" in cfg.enabled_stages:
        try:
            nb["messages"] = dedup_tool_results(
                nb.get("messages"), stats.__dict__, min_chars=cfg.dedup_min_chars)
        except Exception as e:
            stats.errors.append(f"dedup:{e}")

    # 5. distill — compress old prose (prompt pruning), keep_last recent untouched
    if "messages" in nb and "distill" in cfg.enabled_stages:
        try:
            nb["messages"] = distill_old_turns(
                nb.get("messages"), stats.__dict__,
                keep_last=cfg.keep_last, max_chars=cfg.distill_max_chars)
        except Exception as e:
            stats.errors.append(f"distill:{e}")

    # 6. budget — pair-safe hard prune (backstop)
    if cfg.token_budget > 0 and "messages" in nb:
        try:
            nb = enforce_budget(nb, cfg.token_budget, keep_last=cfg.keep_last, stats=stats.__dict__)
        except Exception as e:
            stats.errors.append(f"budget:{e}")

    stats.tokens_out = estimate_tokens_obj(nb)
    return nb, stats


# ── Chunked path helper ──────────────────────────────────────────────────────
def minify_chunked_first_event(event_bytes: bytes, cfg: MinifyConfig):
    """Minify the first SSE ``data: {...}`` event of a streaming request.

    Returns (new_event_bytes, stats). On ANY parse problem returns the input
    unchanged with empty stats (caller falls back to raw passthrough). Most
    tool/system mass lands in the first event of a streaming /v1/messages POST.
    """
    import json
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
        obj = json.loads(payload)
    except Exception:
        return event_bytes, MinifyStats()
    if not isinstance(obj, dict):
        return event_bytes, MinifyStats()
    new_obj, stats = minify_request(obj, cfg)
    new_payload = json.dumps(new_obj)
    new_event = (text[:idx + 6] + new_payload + text[nl:]).encode("utf-8")
    return new_event, stats