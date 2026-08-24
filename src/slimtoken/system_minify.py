
from __future__ import annotations

import re

from .context_prune import strip_low_value, collapse_whitespace
from .message_minify import split_fences




_BANNERISH = re.compile(r"^[=\-─│*_~#]+$")


def minify_system(text: str) -> str:

    if not text:
        return text
    out = []
    last_banner: str | None = None
    for is_fence, seg in split_fences(text):
        if is_fence:
            out.append(seg)
            last_banner = None
            continue

        seg = strip_low_value(seg)
        seg = collapse_whitespace(seg)

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