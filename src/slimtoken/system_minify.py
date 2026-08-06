"""system_minify — fence-aware system-prompt minifier.

System prompts carry semantic ``<tag>`` memory markers (e.g.
``<cold_memory>``, ``<recent_context>``) and fenced rules/code. Those MUST be
preserved verbatim — the tags are parsed downstream, and code is code.

Outside fences we apply ``strip_low_value`` + ``collapse_whitespace`` (both
from the existing stdlib pruner) and trim repeated section-banner lines. Inside
fences: verbatim. ``<tag>`` markers are left untouched everywhere (they live
outside fences in practice and are short lines we never collapse).
"""
from __future__ import annotations

import re

from .context_prune import strip_low_value, collapse_whitespace
from .message_minify import split_fences

# A "section banner" = a line that is mostly repeated, e.g. "───── Rules ─────"
# repeated, or a pure-decoration rule line. We only collapse EXACT duplicate
# consecutive banner-ish lines, never unique content.
_BANNERISH = re.compile(r"^[=\-─│*_~#]+$")


def minify_system(text: str) -> str:
    """Minify a system prompt string, preserving tags + code fences."""
    if not text:
        return text
    out = []
    last_banner: str | None = None
    for is_fence, seg in split_fences(text):
        if is_fence:
            out.append(seg)
            last_banner = None
            continue
        # Outside a fence: strip low value + collapse whitespace.
        seg = strip_low_value(seg)
        seg = collapse_whitespace(seg)
        # Collapse consecutive duplicate banner/decoration lines.
        lines = []
        for ln in seg.split("\n"):
            if _BANNERISH.match(ln.strip()):
                if ln == last_banner:
                    continue
                last_banner = ln
            else:
                last_banner = None
            lines.append(ln)
        out.append("\n".join(lines))
    result = "".join(out)
    return result.strip()