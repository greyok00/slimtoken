#!/usr/bin/env python3

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, List, Optional, Sequence




_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]")
_ESC2_RE = re.compile(r"\x1b[@-_]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_terminal(text: str) -> str:

    if not text:
        return text
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub("", text)
    text = _ESC2_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text



class TurnState(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TurnEvent:
    pass


@dataclass
class TurnStarted(TurnEvent):
    model: str = ""
    started_at: float = 0.0


@dataclass
class TextDelta(TurnEvent):
    text: str = ""


@dataclass
class StatusDelta(TurnEvent):
    text: str = ""


@dataclass
class TurnCompleted(TurnEvent):
    text: str = ""
    elapsed: float = 0.0


@dataclass
class TurnFailed(TurnEvent):
    error: str = ""


@dataclass
class TurnCancelled(TurnEvent):
    reason: str = "cancelled"



@dataclass
class CodeArtifact:
    id: int
    language: str = "text"
    filename: Optional[str] = None
    lines: List[str] = field(default_factory=list)

    @property
    def n_lines(self) -> int:
        return len(self.lines)

    @property
    def source(self) -> str:
        return "\n".join(self.lines)


class ResponseBlock:
    pass


@dataclass
class TextBlock(ResponseBlock):
    text: str = ""


@dataclass
class ArtifactBlock(ResponseBlock):
    artifact: CodeArtifact = None  # type: ignore[assignment]
    collapsed: bool = True


@dataclass
class DisclosureBlock(ResponseBlock):
    title: str = ""
    detail: str = ""
    collapsed: bool = True


@dataclass
class ToolBlock(ResponseBlock):
    summary: str = ""
    status: str = "ok"
    elapsed: str = ""



_LANG_ALIASES = {
    "js": "javascript", "ts": "typescript", "py": "python", "rb": "ruby",
    "go": "go", "rs": "rust", "sh": "bash", "bash": "bash", "zsh": "bash",
    "yml": "yaml", "yaml": "yaml", "json": "json", "md": "markdown",
    "c": "c", "cpp": "cpp", "h": "c", "java": "java", "sql": "sql",
    "html": "html", "css": "css", "diff": "diff", "patch": "diff",
    "text": "text", "txt": "text", "console": "text", "log": "text",
}
_EXT_BY_LANG = {
    "python": ".py", "javascript": ".js", "typescript": ".ts", "bash": ".sh",
    "json": ".json", "yaml": ".yml", "diff": ".diff", "sql": ".sql",
    "html": ".html", "css": ".css", "go": ".go", "rust": ".rs", "java": ".java",
    "c": ".c", "cpp": ".cpp", "markdown": ".md", "text": ".txt",
}

_CODE_HINT = re.compile(
    r"(^\s*(def|class|import|from|const|let|var|function|func|public|private|"
    r"static|interface|type|struct|package|#include|using|return|echo|print|"
    r"SELECT|INSERT|UPDATE|CREATE|DROP) |[{}=;>\]]\s*$|=>|\{\}|\{\s*$)",
    re.MULTILINE,
)


def detect_language(fence_info: Optional[str], lines: Sequence[str]) -> str:

    if fence_info:
        lang = fence_info.strip().lower().split()[0]
        return _LANG_ALIASES.get(lang, lang)
    head = "\n".join(lines[:8])
    if re.search(r"^[+-]", head, re.MULTILINE) and re.search(
            r"^(diff --git|@@ |Index: |--- |\+\+\+ )", head, re.MULTILINE):
        return "diff"
    if lines and lines[0].lstrip().startswith(("Traceback", "File ")):
        return "traceback"
    if lines and lines[0].lstrip().startswith(("{", "[")):
        return "json"
    if _CODE_HINT.search(head):


        if (re.search(r"^(def|class|import|from)\s+[A-Za-z_]\w*\s*[:(]",
                      head, re.MULTILINE)
                or re.search(r"^\s*(import|from)\s+[A-Za-z_]", head, re.MULTILINE)):
            return "python"
        if re.search(r"^#!", head, re.MULTILINE) or re.search(r"^\s*echo\s",
                                                              head, re.MULTILINE):
            return "bash"
        return "text/code"
    return "text"


def infer_filename(lang: str, lines: Sequence[str], index: int) -> str:

    for ln in lines:
        m = re.match(r"\s*(?:async\s+)?(?:def|class|function|func|fn)\s+([A-Za-z_]\w*)", ln)
        if m:
            base = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", m.group(1)).lower()
            return f"{base}{_EXT_BY_LANG.get(lang, '.txt')}"
    base = f"artifact_{index}"
    return f"{base}{_EXT_BY_LANG.get(lang, '.txt')}"



_FENCE_RE = re.compile(r"^[ \t]*```([^\n`]*)")
_INDENT_RE = re.compile(r"^(?: {4}|\t)")
_DIFF_RE = re.compile(r"^[+-].*$")
_DIFF_HDR_RE = re.compile(r"^(diff --git|@@ |Index: |--- |\+\+\+ |=== )")


def _looks_like_code(lines: Sequence[str]) -> bool:

    if len(lines) < 3:
        return False
    head = "\n".join(lines[:10])
    return bool(_CODE_HINT.search(head))


def _is_diff_block(lines: Sequence[str]) -> bool:
    nonblank = [l for l in lines if l.strip()]
    if not nonblank:
        return False
    hdr = sum(1 for l in lines if _DIFF_HDR_RE.match(l))
    chg = sum(1 for l in lines if _DIFF_RE.match(l))
    return hdr >= 1 and chg >= 2


def _looks_like_json(lines: Sequence[str]) -> bool:
    if not lines:
        return False
    first = lines[0].lstrip()
    return first.startswith(("{", "["))


def _text_clean(lines: Sequence[str]) -> str:
    return "\n".join(l.rstrip() for l in lines).strip("\n")


def parse_response(text: str, max_artifacts: int = 100) -> List[ResponseBlock]:

    text = sanitize_terminal(text)
    lines = text.splitlines()
    blocks: List[ResponseBlock] = []
    buf: List[str] = []
    art_id = 0

    def flush_text() -> None:
        nonlocal buf
        if buf:
            cleaned = _text_clean(buf)
            if cleaned:
                blocks.append(TextBlock(cleaned))
            buf = []

    def add_artifact(lang: str, code: Sequence[str], explicit: bool) -> None:
        nonlocal art_id
        if not code:
            return
        art_id += 1
        lang = detect_language(lang if explicit else None, code)
        artifact = CodeArtifact(
            id=art_id,
            language=lang,
            filename=infer_filename(lang, code, art_id),
            lines=list(code),
        )
        blocks.append(ArtifactBlock(artifact=artifact, collapsed=True))

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            flush_text()
            lang = fence.group(1).strip()
            j = i + 1
            code: List[str] = []
            while j < n and not _FENCE_RE.match(lines[j]):
                code.append(lines[j])
                j += 1
            add_artifact(lang, code, explicit=True)
            i = j + 1 if j < n else j
            continue

        if line.lstrip().startswith("diff --git") or _DIFF_HDR_RE.match(line):
            j = i
            run: List[str] = []
            while j < n and (_DIFF_HDR_RE.match(lines[j]) or
                             (lines[j].startswith(("+", "-")) and
                              not lines[j].startswith(("+++", "---"))) or
                             (lines[j].startswith(("+++", "---")))):
                run.append(lines[j])
                j += 1
            if len(run) >= 2 and _is_diff_block(run):
                flush_text()
                add_artifact("diff", run, explicit=True)
                i = j
                continue
        if _looks_like_json(lines[i:i + 1]) or _looks_like_json(lines[i:i + 2]):
            j = i
            run = []
            while j < n and not lines[j].strip().startswith(("```", "# ")) and \
                    (lines[j].lstrip().startswith(("{", "[", "}", "]", '"', ","))):
                run.append(lines[j])
                j += 1
            if len(run) >= 2 and _looks_like_json(run):
                flush_text()
                add_artifact("json", run, explicit=True)
                i = j
                continue
        if _INDENT_RE.match(line) and line.strip():
            j = i
            run = []
            while j < n and (_INDENT_RE.match(lines[j]) or not lines[j].strip()):
                run.append(lines[j].lstrip() if lines[j].strip() else "")
                j += 1
            if _looks_like_code(run):
                flush_text()
                add_artifact("", run, explicit=False)
                i = j
                continue

        buf.append(line)
        i += 1
    flush_text()
    return blocks



def collapse(
    blocks: Sequence[ResponseBlock],
    *,
    max_text: int = 1500,
    max_visible_artifacts: int = 0,
    max_tool_events: int = 8,
) -> List[ResponseBlock]:

    out: List[ResponseBlock] = []
    kept_text = 0
    tail_text: List[str] = []
    hidden_art: List[ArtifactBlock] = []
    tools: List[ToolBlock] = []
    visible_art = 0

    for b in blocks:
        if isinstance(b, TextBlock):
            room = max_text - kept_text
            if room > 0:
                if len(b.text) <= room:
                    out.append(TextBlock(b.text))
                    kept_text += len(b.text)
                else:
                    out.append(TextBlock(b.text[:room].rstrip() + "…"))
                    tail_text.append(b.text[room:].strip())
                    kept_text = max_text
            else:
                if b.text.strip():
                    tail_text.append(b.text.strip())
        elif isinstance(b, ArtifactBlock):
            if visible_art < max_visible_artifacts:
                out.append(b)
                visible_art += 1
            else:
                hidden_art.append(b)
        elif isinstance(b, ToolBlock):
            tools.append(b)
        elif isinstance(b, DisclosureBlock):
            out.append(b)

    if hidden_art:
        detail = "\n".join(
            f"[{a.artifact.language}] {a.artifact.filename} · {a.artifact.n_lines} lines"
            for a in hidden_art)
        out.append(DisclosureBlock(
            f"Code artifacts ({len(hidden_art)})", detail, collapsed=True))

    visible_tools = tools[:max_tool_events]
    out.extend(visible_tools)
    if len(tools) > max_tool_events:
        rest = tools[max_tool_events:]
        detail = "\n".join(f"{t.status}: {t.summary}" for t in rest)
        out.append(DisclosureBlock(
            f"Tool activity ({len(rest)})", detail, collapsed=True))

    if tail_text:
        out.append(DisclosureBlock(
            f"Details ({len(tail_text)})", "\n\n".join(tail_text), collapsed=True))
    return out



def wrap_text(text: str, width: int = 80) -> List[str]:

    width = max(10, width)
    out: List[str] = []
    for para in text.split("\n"):
        if not para.strip():
            if out:
                out.append("")
            continue
        words = para.split(" ")
        cur = ""
        for w in words:
            if len(cur) + 1 + len(w) > width:
                if cur:
                    out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            out.append(cur)
    return out






_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
        "lm": "├", "rm": "┤", "tm": "┬", "bm": "┴", "x": "┼"}
_ART_CHARS = set("│─┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬"
                 "█▓▒░▄▀▌▐▂▃▅▆▇◤◥◢◣◰◱◲◳○●▲▼")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


def _is_sep(line: str) -> bool:

    s = line.strip().strip("|")
    return bool(s) and "---" in s and all(ch in " :-|" for ch in s)


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _trunc(cell: str, w: int) -> str:
    return cell if len(cell) <= w else cell[:max(1, w - 1)] + "…"


def _render_table(lines: Sequence[str], width: int) -> List[str]:

    rows = [_split_row(l) for l in lines]
    sep_idx = next((i for i, l in enumerate(lines) if _is_sep(l)), None)
    header = None
    if sep_idx is not None and sep_idx > 0:
        header = rows[sep_idx - 1]
        rows = rows[sep_idx + 1:]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    ncols = max(len(r) for r in rows + ([header] if header else []))

    def pad(r: Sequence[str]) -> List[str]:
        return list(r) + [""] * (ncols - len(r))

    rows = [pad(r) for r in rows]
    if header:
        header = pad(header)

    widths = [0] * ncols
    for r in rows + ([header] if header else []):
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    budget = max(3, width - 3 * ncols - 1)
    if sum(widths) > budget:
        scale = budget / max(1, sum(widths))
        widths = [max(2, int(w * scale)) for w in widths]
        total = sum(widths)
        while total > budget and max(widths) > 2:
            j = widths.index(max(widths))
            widths[j] -= 1
            total -= 1

    def row_line(r: Sequence[str]) -> str:
        cells = [_trunc(c, widths[i]) for i, c in enumerate(r)]
        return (_BOX["v"] + _BOX["v"].join(" %-*s " % (widths[i], cells[i])
                                           for i in range(ncols))
                + _BOX["v"])

    def hr(left: str, mid: str, right: str) -> str:
        return (left + mid.join(_BOX["h"] * (widths[i] + 2) for i in range(ncols))
                + right)

    out = [hr(_BOX["tl"], _BOX["tm"], _BOX["tr"])]
    if header:
        out.append(row_line(header))
        out.append(hr(_BOX["lm"], _BOX["x"], _BOX["rm"]))
    for r in rows:
        out.append(row_line(r))
    out.append(hr(_BOX["bl"], _BOX["bm"], _BOX["br"]))
    return out


def _numeric(v: str) -> Optional[float]:
    t = v.strip().replace(",", "").replace("$", "").replace("%", "")
    try:
        return float(t)
    except ValueError:
        return None


def _make_chart(header, rows: Sequence[Sequence[str]], width: int
                ) -> Optional[List[str]]:

    if not rows or len(rows) < 2:
        return None
    ncols = max(len(r) for r in rows)
    if ncols < 2:
        return None
    best_col, best_cnt, best_spread = None, 0, 0.0
    for c in range(ncols):
        vals = [v for r in rows if c < len(r)
                for v in [_numeric(r[c])] if v is not None]
        if len(vals) < 2:
            continue
        spread = max(vals) - min(vals)
        if len(vals) > best_cnt or (len(vals) == best_cnt and spread > best_spread):
            best_col, best_cnt, best_spread = c, len(vals), spread
    if best_col is None:
        return None
    label_col = next((c for c in range(ncols)
                      if c != best_col and all(c >= len(r) or _numeric(r[c]) is None
                                               for r in rows)), None)
    if label_col is None:
        label_col = 0 if best_col != 0 else 1
    pairs = [(r[label_col] if label_col < len(r) else "", _numeric(r[best_col]))
             for r in rows if best_col < len(r) and _numeric(r[best_col]) is not None]
    if len(pairs) < 2:
        return None
    max_v = max(v for _, v in pairs)
    if max_v <= 0:
        return None
    label_w = max(len(l) for l, _ in pairs)
    bar_max = max(4, min(40, width - label_w - 12))
    head = header[best_col] if header and best_col < len(header) else "value"
    out = [f"▸ {head}"]
    for lbl, v in pairs:
        n = int(round(bar_max * v / max_v))
        out.append(f"  {lbl:<{label_w}} {'█' * n} {v:g}")
    return out


def _chart_for_table(lines: Sequence[str], width: int) -> Optional[List[str]]:
    rows = [_split_row(l) for l in lines]
    sep_idx = next((i for i, l in enumerate(lines) if _is_sep(l)), None)
    header = None
    if sep_idx is not None and sep_idx > 0:
        header = rows[sep_idx - 1]
        rows = rows[sep_idx + 1:]
    rows = [r for r in rows if any(c.strip() for c in r)]
    return _make_chart(header, rows, width)


def _looks_art(line: str) -> bool:
    return any(ch in _ART_CHARS for ch in line)


def format_visual(text: str, width: int = 80, charts: bool = True) -> str:

    lines = text.split("\n")
    out: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            out.extend(wrap_text("\n".join(buf), width))
            buf = []

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("|"):
            j = i
            run: List[str] = []
            while j < n and lines[j].lstrip().startswith("|"):
                run.append(lines[j])
                j += 1
            if len(run) >= 2 and (any(_is_sep(l) for l in run) or len(run) >= 3):
                flush()
                out.extend(_render_table(run, width))
                if charts:
                    ch = _chart_for_table(run, width)
                    if ch:
                        out.extend(ch)
                i = j
                continue
        h = _HEADING_RE.match(line)
        if h:
            flush()
            out.append("▎ " + h.group(1).strip())
            i += 1
            continue
        if _looks_art(line):
            flush()
            out.append(line.rstrip())
            i += 1
            continue
        buf.append(line)
        i += 1
    flush()
    return "\n".join(out)


def render_plain(blocks: Sequence[ResponseBlock], width: int = 80,
                 charts: bool = True) -> str:

    lines: List[str] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            lines.extend(format_visual(b.text, width, charts=charts).split("\n"))
        elif isinstance(b, ArtifactBlock):
            a = b.artifact
            lines.append(f"[Code artifact: {a.filename} · {a.language} · "
                         f"{a.n_lines} lines]")
            lines.append("```" + (a.language or ""))
            lines.extend(a.lines)
            lines.append("```")
        elif isinstance(b, ToolBlock):
            mark = {"ok": "✓", "running": "●", "failed": "!"}.get(b.status, "•")
            suffix = f" · {b.elapsed}" if b.elapsed else ""
            lines.append(f"{mark} {b.summary}{suffix}")
        elif isinstance(b, DisclosureBlock):
            lines.append(f"▸ {b.title}")
            if not b.collapsed:
                lines.extend(wrap_text(b.detail, width))
    return "\n".join(lines)


def summarize(text: str, limit: int = 240) -> str:

    text = sanitize_terminal(text).strip()
    if not text:
        return ""
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    if len(first) <= limit:
        return first
    cut = first[:limit]

    for sep in (". ", "? ", "! ", " "):
        idx = cut.rfind(sep)
        if idx > limit // 2:
            return cut[:idx + 1] + "…"
    return cut.rstrip() + "…"


def stream_turn(text: str, model: str = "", started_at: float = 0.0
                ) -> Iterator[TurnEvent]:

    yield TurnStarted(model=model, started_at=started_at)
    if text:
        yield TextDelta(text=text)
    yield TurnCompleted(text=text, elapsed=0.0)
