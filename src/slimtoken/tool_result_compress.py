"""tool_result_compress — lossy, type-specific tool-result reducers.

Opt-in (``SLIMTOKEN_TOOL_COMPRESS=1``). When ON, each ``tool_result`` content
block is inspected for a recognizable shape (directory listing, git output, log
dump, JSON, source code) and replaced with a compact representation plus a
metadata header. This is **lossy** — that's why it's off by default and gated
separately from the lossless minify pipeline.

Pair-safety: only the ``content`` field of a ``tool_result`` block is rewritten.
Messages are never removed or reordered, and the preceding ``tool_use`` is
untouched, so the tool_use/tool_result pairing invariant the rest of the
pipeline depends on is preserved.
"""
from __future__ import annotations

import re
from typing import Optional

# Cap how much of any single tool_result we keep after compression. The point
# is to kill context bloat from giant tool outputs, not to be a perfect renderer.
_MAX_KEPT = 60

_META_PREFIX = "[slimtoken-compressed] "


def _text_of(content) -> str:
    """Flatten a tool_result `content` (str OR list of text blocks) to a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def _wrap(orig_bytes: int, body: str) -> str:
    head = f"{_META_PREFIX}{orig_bytes}B -> {len(body.encode())}B; "
    return head + body


# ── shape detectors / reducers ────────────────────────────────────────────────
def _try_directory_listing(text: str) -> Optional[str]:
    """`ls -la` / `dir` style blocks: lots of permission/mode/size lines."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 8:
        return None
    # ls -la lines look like: drwxr-xr-x  2 user group 4096 Jan 1 12:00 name
    ls_re = re.compile(r'^[dls-]?[rwx-]{9,10}\s+\d+\s+\S+\s+\S+\s+\d+\s+')
    hits = sum(1 for l in lines if ls_re.match(l))
    if hits < max(6, len(lines) * 0.6):
        return None
    names = []
    for l in lines:
        m = ls_re.match(l)
        if m:
            # last whitespace-separated token is the name
            tail = l[m.end():].strip()
            names.append(tail.split()[-1] if tail else "")
    names = [n for n in names if n and n not in (".", "..")]
    body = "dir listing: " + ", ".join(names[:_MAX_KEPT])
    if len(names) > _MAX_KEPT:
        body += f" ... (+{len(names) - _MAX_KEPT} more)"
    return body


def _try_git(text: str) -> Optional[str]:
    if "git " not in text and "commit " not in text[:40]:
        return None
    lines = text.splitlines()
    out = []
    for l in lines:
        s = l.strip()
        if s.startswith(("commit ", "Author:", "Date:", "Merge:")):
            out.append(l)
        elif re.match(r"^\+\+\+ |^--- |^@@|^\+|^[-]\s", s) and ("+++" in s or "---" in s or s.startswith("@@") or s.startswith("+") or s.startswith("-")):
            # diff line — keep just +/- markers, capped
            out.append(s[:120])
        if len(out) >= _MAX_KEPT:
            break
    if not out:
        return None
    return "git: " + " | ".join(out[:_MAX_KEPT])


def _try_log(text: str) -> Optional[str]:
    """Log dumps: many timestamped lines. Keep first/last few + count."""
    lines = text.splitlines()
    if len(lines) < 20:
        return None
    ts_re = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}[\dT ][\d:.\-]+\s*")
    iso = sum(1 for l in lines if ts_re.match(l.strip()))
    if iso < max(10, len(lines) * 0.5):
        return None
    head = [l.strip()[:160] for l in lines[:5]]
    tail = [l.strip()[:160] for l in lines[-5:]]
    body = f"log {len(lines)} lines:\n" + "\n".join(head) + "\n...\n" + "\n".join(tail)
    return body


def _try_json(text: str) -> Optional[str]:
    import json as _j
    s = text.strip()
    if not s or s[0] not in "[{":
        return None
    try:
        obj = _j.loads(s)
    except Exception:
        return None
    try:
        compact = _j.dumps(obj, separators=(",", ":"))
    except Exception:
        return None
    # fire if compacting removes a meaningful amount of whitespace
    if len(compact) < len(s) * 0.85:
        return "json: " + compact[:4000]
    return None


def _try_source(text: str) -> Optional[str]:
    """Source code: drop blank lines + comments, keep structure."""
    lines = text.splitlines()
    if len(lines) < 12:
        return None
    # heuristic: balanced-ish braces / indentation / common keywords
    has_indent = sum(1 for l in lines if l[:1].isspace()) > len(lines) * 0.3
    keywords = sum(1 for l in lines if re.search(r"\b(def|class|function|return|if|for|import|const|var|public)\b", l))
    if not (has_indent or keywords > 3):
        return None
    kept = []
    for l in lines:
        s = l.rstrip()
        if not s.strip():
            continue
        # drop full-line comments (keep inline ones)
        st = s.lstrip()
        if st.startswith(("//", "#")) and not st.startswith(("#!", "#pragma")):
            continue
        kept.append(s[:160])
        if len(kept) >= _MAX_KEPT:
            break
    return "source:\n" + "\n".join(kept)


_REDUCERS = (_try_directory_listing, _try_git, _try_log, _try_json, _try_source)


def compress_text(text: str) -> Optional[str]:
    """Return a compressed representation, or None if no shape matched.

    The caller decides whether to apply it (it's lossy)."""
    if not text or len(text) < 200:
        return None
    for r in _REDUCERS:
        try:
            out = r(text)
        except Exception:
            out = None
        if out:
            return _wrap(len(text.encode()), out)
    return None


def compress_content(content):
    """Rewrite a tool_result `content` field (str or list-of-blocks).

    Returns (new_content, changed). Only text is touched; non-text blocks
    (images, etc.) pass through. Pair-safety: only the content field changes."""
    if isinstance(content, str):
        nc = compress_text(content)
        return (nc, True) if nc else (content, False)
    if isinstance(content, list):
        changed = False
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                nc = compress_text(b["text"])
                if nc:
                    out.append({**b, "text": nc})
                    changed = True
                    continue
            out.append(b)
        return (out if changed else content, changed)
    return (content, False)


def compress_messages(messages) -> tuple:
    """Compress tool_result blocks across the message list. Pair-safe.

    Returns (new_messages, count). new_messages is the original list if
    nothing changed (zero-copy no-op)."""
    if not isinstance(messages, list):
        return messages, 0
    new = []
    changed = False
    count = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            new.append(msg); continue
        content = msg.get("content")
        if not isinstance(content, list):
            new.append(msg); continue
        nc = []
        local = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                rc = block.get("content")
                cc, did = compress_content(rc)
                if did:
                    nb = dict(block); nb["content"] = cc
                    nc.append(nb); local = True; count += 1
                    continue
            nc.append(block)
        if local:
            new.append({**msg, "content": nc}); changed = True
        else:
            new.append(msg)
    return (new if changed else messages), count