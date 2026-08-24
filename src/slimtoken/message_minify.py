
from __future__ import annotations

import re
from typing import List, Tuple, Union




_FENCE_OPEN = re.compile(r"^[ \t]*(```|~~~)(.*)$")


def split_fences(text: str) -> List[Tuple[bool, str]]:

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

            if line.strip() == marker:
                flush(True)
                in_fence = False
                marker = None
    if in_fence:

        flush(True)
    elif buf:
        flush(False)
    return segments


def _minify_text_outside_fence(text: str) -> str:


    text = re.sub(r"\n{3,}", "\n\n", text)


    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text


def minify_text(text: str) -> str:

    if not text:
        return text
    out = []
    for is_fence, seg in split_fences(text):
        out.append(seg if is_fence else _minify_text_outside_fence(seg))


    text = "".join(out)
    text = re.sub(r"^\n+", "", text)
    text = re.sub(r"\n+$", "", text)
    return text



_TEXT_BLOCK = "text"


def minify_message_content(content: Union[str, list]) -> Union[str, list]:

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