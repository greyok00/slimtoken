"""message_minify — code-block-aware content minifier.

CRITICAL contract: this is a coding agent. Code inside ``` / ~~~ fences is
sacred and MUST pass through byte-identical. The ONLY transforms applied
outside fences are semantically null: collapse 3+ blank lines to 1, strip
trailing per-line whitespace, and trim leading/trailing blank lines. We
deliberately do NOT run ``strip_low_value`` on message text — that risks
dropping user intent ("I think …" is sometimes the whole point).

Handles Anthropic message ``content`` in either form:
  - a plain string
  - a list of content blocks: {"type": "text"|"tool_use"|"tool_result"|…}

Only ``text`` blocks are transformed. ``tool_use`` / ``tool_result`` /
``image`` / anything else pass through UNCHANGED — tool I/O is verbatim.
"""
from __future__ import annotations

import re
from typing import List, Tuple, Union

# A fence opener: optional leading whitespace, then ``` or ~~~, optional info
# string. Commonmark lets ~~~ open a block that ``` closes only by ~~~, so we
# track which marker opened and require the same marker to close.
_FENCE_OPEN = re.compile(r"^[ \t]*(```|~~~)(.*)$")


def split_fences(text: str) -> List[Tuple[bool, str]]:
    """Split text into (is_fence, segment) pairs.

    ``is_fence`` True = inside a code block — leave verbatim.
    On a malformed fence (open, no close) we treat the REST of the text as
    fenced — over-preserve rather than risk corrupting code by treating it as
    prose. A mismatched closer with no opener is preserved verbatim too (it
    stays a single un-fenced segment but is left untouched by callers, since
    the only outside-fence transforms are null-op whitespace normalization).
    """
    segments: List[Tuple[bool, str]] = []
    buf: List[str] = []
    in_fence = False
    marker = None

    def flush(is_fenced: bool):
        if buf:
            segments.append((is_fenced, "".join(buf)))
            buf.clear()

    for line in text.splitlines(keepends=True):
        if not in_fence:
            m = _FENCE_OPEN.match(line.rstrip("\n"))
            # Only treat as an opener if the line is just the fence marker
            # (plus optional info string) — i.e. the rstrip matches the pattern
            # and there's nothing after the info string worth splitting on.
            if m and line.strip() == (m.group(1) + m.group(2).strip()) or (
                m and m.group(2).strip() == ""
            ):
                flush(False)
                in_fence = True
                marker = m.group(1)
                buf.append(line)
                continue
            buf.append(line)
        else:
            buf.append(line)
            # A closer: a line whose stripped form is exactly the open marker.
            if line.strip() == marker:
                flush(True)
                in_fence = False
                marker = None
    if in_fence:
        # Malformed: open fence never closed. Over-preserve as fenced.
        flush(True)
    elif buf:
        flush(False)
    return segments


def _minify_text_outside_fence(text: str) -> str:
    """Null-op-ish whitespace normalization for prose (outside code fences).

    Intentionally does NOT strip leading/trailing newlines of the segment —
    a trailing blank line is the boundary that keeps the next fence opener on
    its own line. Stripping it would fuse prose onto the ```` ``` ```` marker
    and break fence detection downstream. Whole-text edge trimming happens once
    in :func:`minify_text`.
    """
    # Collapse 3+ blank lines to a single blank line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip trailing whitespace on each line (never leading — indentation can
    # be meaningful in markdown lists).
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text


def minify_text(text: str) -> str:
    """Minify a free-text string, preserving code fences verbatim."""
    if not text:
        return text
    out = []
    for is_fence, seg in split_fences(text):
        out.append(seg if is_fence else _minify_text_outside_fence(seg))
    # Trim leading/trailing blank lines of the WHOLE text only (not per segment,
    # which would fuse segments across fence boundaries).
    text = "".join(out)
    text = re.sub(r"^\n+", "", text)
    text = re.sub(r"\n+$", "", text)
    return text


# Anthropic content block types we transform (text only).
_TEXT_BLOCK = "text"


def minify_message_content(content: Union[str, list]) -> Union[str, list]:
    """Minify message ``content`` — string or Anthropic content-block list.

    Only ``text`` blocks are touched. Everything else (tool_use, tool_result,
    image, …) is returned UNCHANGED. Tool-result nested content is left
    verbatim on purpose: a file read inside a tool_result is effectively code.

    Returns the ORIGINAL object (identity-preserving) when nothing changed, so
    callers can detect a no-op with a cheap ``is`` check instead of serializing.
    """
    if isinstance(content, str):
        new = minify_text(content)
        return new if new != content else content
    if not isinstance(content, list):
        return content
    new_blocks = []
    changed = False
    for block in content:
        if (isinstance(block, dict) and block.get("type") == _TEXT_BLOCK
                and isinstance(block.get("text"), str)):
            nt = minify_text(block["text"])
            if nt != block["text"]:
                nb = dict(block)
                nb["text"] = nt
                new_blocks.append(nb)
                changed = True
                continue
        new_blocks.append(block)
    return new_blocks if changed else content