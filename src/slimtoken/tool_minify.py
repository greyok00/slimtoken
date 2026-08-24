
from __future__ import annotations

import re
from typing import Dict, Set

from .message_minify import split_fences, minify_text


_SCHEMA_NOISE_LEAF = {"$comment", "title", "examples"}



_PREFIX_PATTERNS = [
    r"^\s*Use this(?: tool)? to\b.*?\.\s+",
    r"^\s*This tool\b.*?\.\s+",
    r"^\s*This (?:function|command)\b.*?\.\s+",
]

_PREFIX_RES = [re.compile(p, re.IGNORECASE) for p in _PREFIX_PATTERNS]


def _strip_schema(schema):

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

    if not desc:
        return desc

    desc = minify_text(desc)



    lines = desc.split("\n")
    cleaned = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            cleaned.append(ln)
            continue

        new = stripped
        for rx in _PREFIX_RES:
            if rx.match(new):
                new = rx.sub("", new).strip()
        cleaned.append(new if new else ln)
    desc = "\n".join(cleaned).strip()


    segs = split_fences(desc)
    fence_count = 0
    kept = []
    for is_fence, seg in segs:
        if is_fence:
            fence_count += 1
            if fence_count > 1:

                continue
        kept.append(seg)
    desc = "".join(kept).strip()
    return desc


def minify_tool(tool: Dict, skip: Set[str]) -> Dict:

    if not isinstance(tool, dict):
        return tool
    name = tool.get("name", "")
    if name in skip:
        return tool
    desc = tool.get("description", "")
    if not isinstance(desc, str) or len(desc) < 200:


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

    if not isinstance(tools, list):
        return tools
    return [minify_tool(t, skip) for t in tools]