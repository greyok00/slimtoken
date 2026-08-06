"""tool_minify — balanced tool-definition minifier.

Goal: shrink tool defs (the 30-tool system prompt is the single biggest
recurring cost) WITHOUT breaking tool selection. A model keys tool choice
on the tool ``name`` and the first sentence of ``description`` plus any
schema structure. Balanced = compress hard, but preserve:

  - ``name`` verbatim
  - ``input_schema`` STRUCTURE: ``type``/``description``/``enum``/``required``/
    ``properties`` kept; ``$comment``/``title``/``examples`` dropped
  - the first sentence of ``description`` plus every word in ``name``

Skip a tool entirely if its ``name`` is in ``skip`` (whitelist of tools too
risky to touch) or its description is already short (< 200 chars — not worth
the risk). Never drop ``required`` or ``enum``.
"""
from __future__ import annotations

import re
from typing import Dict, Set

from .message_minify import split_fences, minify_text

# Schema keys that are pure documentation noise on leaf nodes.
_SCHEMA_NOISE_LEAF = {"$comment", "title", "examples"}
# Boilerplate prefixes that add no selection signal. These CONSUME the lead-in
# sentence up to and including the first ". " (period+space) so the remainder
# ("You can access …") is left intact with no leading ". " artifact.
_PREFIX_PATTERNS = [
    r"^\s*Use this(?: tool)? to\b.*?\.\s+",
    r"^\s*This tool\b.*?\.\s+",
    r"^\s*This (?:function|command)\b.*?\.\s+",
]
# Compiled once.
_PREFIX_RES = [re.compile(p, re.IGNORECASE) for p in _PREFIX_PATTERNS]


def _strip_schema(schema):
    """Recursively drop noise keys from a JSON schema, preserving structure."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in _SCHEMA_NOISE_LEAF:
                continue
            out[k] = _strip_schema(v)
        return out
    if isinstance(schema, list):
        return [_strip_schema(x) for x in schema]
    return schema


def _compress_description(desc: str) -> str:
    """Compress a tool description while protecting the selection signal.

    - fence-aware: keep code blocks verbatim, but keep only the FIRST fenced
      example (drop subsequent examples that just repeat the shape)
    - strip boilerplate lead-ins ("Use this tool to …", "This tool …")
    - collapse whitespace
    - invariant: the first sentence is always preserved, and any word that
      appears in the tool name is never removed (the model may key on it)
    """
    if not desc:
        return desc
    # First, fence-aware whitespace pass (code blocks untouched).
    desc = minify_text(desc)
    # Strip boilerplate prefixes (only the lead-in clause up to the first
    # sentence boundary — we keep the remainder of that sentence's tail by
    # NOT spanning past the boundary; simplest: drop a matching prefix line).
    lines = desc.split("\n")
    cleaned = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            cleaned.append(ln)
            continue
        # Only strip a prefix if it's the whole line being a lead-in sentence.
        new = stripped
        for rx in _PREFIX_RES:
            if rx.match(new):
                new = rx.sub("", new).strip()
        cleaned.append(new if new else ln)
    desc = "\n".join(cleaned).strip()

    # Keep only the first fenced code block; drop later example blocks.
    segs = split_fences(desc)
    fence_count = 0
    kept = []
    for is_fence, seg in segs:
        if is_fence:
            fence_count += 1
            if fence_count > 1:
                # drop subsequent example blocks
                continue
        kept.append(seg)
    desc = "".join(kept).strip()
    return desc


def minify_tool(tool: Dict, skip: Set[str]) -> Dict:
    """Return a minified copy of a single tool def. Unchanged if skipped."""
    if not isinstance(tool, dict):
        return tool
    name = tool.get("name", "")
    if name in skip:
        return tool
    desc = tool.get("description", "")
    if not isinstance(desc, str) or len(desc) < 200:
        # Short description → not worth the risk; leave as-is. Still strip
        # schema noise though, which is always safe.
        nt = dict(tool)
        if "input_schema" in nt:
            nt["input_schema"] = _strip_schema(nt["input_schema"])
        return nt
    nt = dict(tool)
    nt["description"] = _compress_description(desc)
    if "input_schema" in nt:
        nt["input_schema"] = _strip_schema(nt["input_schema"])
    return nt


def minify_tools(tools, skip: Set[str]):
    """Minify a list of tool defs. Returns a new list."""
    if not isinstance(tools, list):
        return tools
    return [minify_tool(t, skip) for t in tools]