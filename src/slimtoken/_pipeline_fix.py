"""Cache-aware dedup stage (audit F4, 2026-10-31).

Why: the shipped ``dedup`` stage stubs the OLDER copy of a byte-identical
tool_result ("identical to a later tool_result"). The first time a duplicate
appears, an EARLIER message in the request changes — which invalidates an
exact-prefix (Anthropic-style / llama.cpp) prompt cache from that point on.

This module implements the append-only policy instead:
  * the FIRST occurrence of a tool_result is kept byte-exact;
  * every LATER byte-identical occurrence is replaced by a digest stub;
  * earlier messages therefore never change, so the cached prefix stays
    valid and only the newly appended tail is (re)processed.

All other stages still run through the compiled pipeline. The artifact flow is
unchanged: dropped bytes are stored under their sha256 digest and are
recoverable with ``slimtoken recall <digest>``.

Activation: ``SLIMTOKEN_DEDUP_APPEND_ONLY=1`` (default ON; set ``0`` to fall
back to the shipped oldest-copy-stubbing behavior). Registered by
``slimtoken/__init__.py`` before any compiled module imports the pipeline.
"""
from __future__ import annotations
import copy
import os
import sys
from typing import Dict, List, Tuple

_NATIVE = None        # compiled pipeline module
_HOOKED = False

_STUB_HEAD = "[slimtoken: identical to an earlier tool_result; omitted"


def _append_only_enabled() -> bool:
    return os.environ.get("SLIMTOKEN_DEDUP_APPEND_ONLY", "1") not in ("0", "false", "no")


def _iter_tool_results(messages: List[dict]):
    """Yield every tool_result block in order (list-content or dict-content)."""
    for m in messages:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    yield b
        elif isinstance(c, dict) and c.get("type") == "tool_result":
            yield c


def _text_of(block) -> Tuple[object, str]:
    """Return (shape, text). shape: 'str' for plain-string content,
    'list' for the Anthropic content-block list form, else None."""
    c = block.get("content")
    if isinstance(c, str):
        return "str", c
    if isinstance(c, list):
        parts = [b.get("text", "") if isinstance(b, dict) else b for b in c]
        return "list", "".join(str(p) for p in parts)
    return None, ""


def _put_text(block, shape, text):
    if shape == "str":
        block["content"] = text
    elif shape == "list":
        first = next((b for b in block["content"]
                      if isinstance(b, dict) and b.get("type") == "text"), None)
        if first is not None:
            first["text"] = text
        else:
            block["content"] = [{"type": "text", "text": text}]


def _stub(text: str, digest: str) -> str:
    return "%s %d chars - digest %s - recall: slimtoken recall %s]" % (
        _STUB_HEAD, len(text), digest, digest)


def _dedup_append_only(messages: List[dict], cfg, store_func, stats) -> int:
    seen: Dict[str, str] = {}
    n = 0
    min_chars = getattr(cfg, "dedup_min_chars", 200) or 0
    for block in _iter_tool_results(messages):
        shape, text = _text_of(block)
        if shape is None or len(text) < min_chars:
            continue
        dig = seen.get(text)
        if dig is None:
            seen[text] = ""
            try:
                d = store_func(text.encode("utf-8"), kind="tool_result", origin="dedup-append")
            except Exception:
                d = None
            if d:
                seen[text] = d
            continue  # first occurrence: keep byte-exact
        if not dig:
            continue  # first copy could not be stored -> never rewrite
        _put_text(block, shape, _stub(text, dig))
        n += 1
    if n and stats is not None:
        try:
            stats.dedup_count = (stats.dedup_count or 0) + n
        except Exception:
            pass
    return n


def apply_fix() -> None:
    global _NATIVE, _HOOKED
    if _HOOKED:
        return
    import importlib
    _NATIVE = importlib.import_module("slimtoken.pipeline")
    _HOOKED = True


def minify_request(body: dict, cfg):
    if not _HOOKED:
        apply_fix()
    if not _append_only_enabled():
        return _NATIVE.minify_request(body, cfg)
    cfg = copy.copy(cfg)
    stages = set(getattr(cfg, "enabled_stages") or set())
    if "dedup" in stages:
        stages.discard("dedup")
        cfg.enabled_stages = stages
    out, stats = _NATIVE.minify_request(body, cfg)
    messages = out.get("messages") if isinstance(out, dict) else None
    if messages is not None:
        try:
            import slimtoken.artifacts as art
            store_func = art.store
        except Exception:
            store_func = None
        if store_func is not None:
            _dedup_append_only(messages, cfg, store_func, stats)
    return out, stats


def __getattr__(name):
    return getattr(_NATIVE, name)


def __dir__():
    return sorted(set(dir(_NATIVE)) | {"minify_request", "apply_fix"})