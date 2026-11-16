"""Loop-guard for served history (2026-11-09, owner-reported compaction loop).

The observed failure mode while the owner's TUI ran under the slimtoken lane:

  a supervisor/steering user message sits at the history tail; the assistant
  answers it; the answer and its tool calls repeat; the harness's loop guard
  blocks the repeat and re-serves the SAME steering user message; slimtoken's
  append-only dedup correctly compacts the repeated tool results, but nothing
  in the served history ever tells the model "this question was already
  answered" — so each following turn reads the bare steering message as a
  fresh instruction and re-answers it, forever.

The guard closes the loop at the history layer, deterministically and with
no cross-request state:

  * ASSISTANT-REPEAT SIGNATURE — an assistant text turn byte-identical to an
    earlier assistant text turn in the SAME request means the model is
    re-answering; the user turn it replies into gets an answered marker so
    the next completion moves forward instead of answering again.
  * USER-REPEAT SIGNATURE — a user turn byte-identical to an earlier user
    turn (the steering re-feed itself) gets the same marker: identical
    steering seen twice in one request can never be a new instruction.

The marker is appended to the user turn's text; it is idempotent (a stamped
turn is never stamped twice — the guard-test law, same as the 0.8.0
non-re-armability guarantees) and carries a fixed instruction, not an
apology. Message contents in code blocks and tool_result blocks are never
touched; only assistant/user prose turns are read, and only user prose is
written.

Activation: ``SLIMTOKEN_LOOPGUARD=1`` (default ON; ``0`` disables cleanly).
Wired by :mod:`slimtoken._pipeline_fix` after the append-only dedup pass, so
both fixes ride the same shim and every entry point (proxy, MCP, CLI)
inherits them.
"""
from __future__ import annotations

import os
from typing import List

MARKER = (
    "[slimtoken: this question was already answered above — resume the task "
    "or report completion; do NOT answer it again]"
)

_STAMP_RE_PRIMER = "[slimtoken:"


def _loopguard_enabled() -> bool:
    return os.environ.get("SLIMTOKEN_LOOPGUARD", "1") not in ("0", "false", "no")


def _assistant_texts(messages: List[dict]):
    """Yield (index, text) for assistant turns whose content is plain prose
    (string or single-text-block). Non-prose (tool_use blocks, lists of
    mixed blocks) is never read: the model's structured turns are not the
    loop surface."""
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            yield i, c
        elif (isinstance(c, list) and len(c) == 1
              and isinstance(c[0], dict) and c[0].get("type") == "text"):
            yield i, str(c[0].get("text") or "")


def _user_blocks(messages: List[dict]):
    """Yield (index, block) for user text blocks (string content or the
    first text block of a list). Only these are ever WRITTEN."""
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            yield i, ("str", None)
        elif isinstance(c, list) and c and isinstance(c[0], dict) \
                and c[0].get("type") == "text":
            yield i, ("list", 0)


def _user_text(messages: List[dict], i: int, shape) -> str:
    c = messages[i].get("content")
    if shape[0] == "str":
        return c if isinstance(c, str) else ""
    if shape[0] == "list" and isinstance(c, list):
        b0 = c[shape[1]]
        return b0.get("text", "") if isinstance(b0, dict) else ""
    return ""


def _stamp(messages: List[dict], i: int, shape) -> bool:
    """Append MARKER to the user turn at index i once. Returns True when
    the message changed. Idempotent: a turn already carrying the marker (or
    any slimtoken stamp) is left alone."""
    c = messages[i].get("content")
    if shape[0] == "str":
        if MARKER in c or _STAMP_RE_PRIMER in c[-120:]:
            return False
        messages[i] = {**messages[i], "content": c + "\n\n" + MARKER}
        return True
    if shape[0] == "list" and isinstance(c, list):
        b0 = dict(c[shape[1]])
        text = str(b0.get("text") or "")
        if MARKER in text or _STAMP_RE_PRIMER in text[-120:]:
            return False
        b0["text"] = text + "\n\n" + MARKER
        nc = list(c)
        nc[shape[1]] = b0
        messages[i] = {**messages[i], "content": nc}
        return True
    return False


def apply_loopguard(messages: List[dict], stats=None) -> int:
    """Stamp loop signatures found in one served request. Returns the count
    of user turns stamped; ``stats`` carries it as ``loopguard_count``."""
    if not isinstance(messages, list) or len(messages) < 3:
        return 0
    if not _loopguard_enabled():
        return 0

    changed = 0

    # SIGNATURE 1: user steering re-fed byte-identically within one request.
    seen_user: dict = {}
    user_shapes = {}
    for i, shape in _user_blocks(messages):
        text = _user_text(messages, i, shape)
        if not text or MARKER in text:
            continue
        if text in seen_user:
            changed += 1  # provisional count; stamping decides
            user_shapes.setdefault(text, (seen_user[text], shape))
        seen_user[text] = i

    for text, (first_i, shape) in user_shapes.items():
        # the SECOND and later identical user turns are the re-feed; stamp
        # from the first duplicate onward
        for i, shape in _user_blocks(messages):
            t = _user_text(messages, i, shape)
            if i > first_i and t == text:
                if _stamp(messages, i, shape):
                    changed += 1

    # SIGNATURE 2: an assistant turn byte-identical to an earlier assistant
    # turn — the model re-answered; stamp the user turn it replies INTO
    # (the nearest preceding user turn without the marker).
    seen_asst: set = set()
    for i, text in _assistant_texts(messages):
        t = text.strip()
        if not t or t in seen_asst:
            # find nearest preceding user turn ≤ i and stamp the LAST one
            # whose text does not already carry the marker
            for j, shape in reversed(
                    [(j, sh) for j, sh in _user_blocks(messages) if j <= i]):
                if MARKER not in _user_text(messages, j, shape):
                    if _stamp(messages, j, shape):
                        changed += 1
                    break
        seen_asst.add(t)

    if changed and stats is not None:
        try:
            stats.loopguard_count = (getattr(stats, "loopguard_count", 0) or 0) + changed
        except Exception:
            pass
    return changed
