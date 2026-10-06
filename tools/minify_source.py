#!/usr/bin/env python3

import ast
import io
import subprocess
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


_KEEP_PREFIXES = ("#!", "# noqa", "# type:", "# type ignore",
                  "# pragma:", "# fmt:", "# pylint:")



_KEEP_MODULE_DOCSTRING = set()


def _line_offsets(src: str):

    offsets = {1: 0}
    line = 1
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\r":
            line += 1
            if i + 1 < n and src[i + 1] == "\n":
                i += 1
            offsets[line] = i + 1
        elif c == "\n":
            line += 1
            offsets[line] = i + 1
        i += 1
    return offsets


def _byte_line_offsets(data: bytes):

    offsets = {1: 0}
    line = 1
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        if c == 0x0D:
            line += 1
            if i + 1 < n and data[i + 1] == 0x0A:
                i += 1
            offsets[line] = i + 1
        elif c == 0x0A:
            line += 1
            offsets[line] = i + 1
        i += 1
    return offsets


def _extend_left(src: str, start: int) -> int:

    i = start
    while i > 0 and src[i - 1] in " \t":
        i -= 1
    return i


def strip_comments(src: str) -> str:

    offsets = _line_offsets(src)
    spans = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            sl, sc = tok.start
            el, ec = tok.end
            text = src[offsets[sl] + sc: offsets[el] + ec]
            if text.startswith(_KEEP_PREFIXES):
                continue
            if text != tok.string:





                tok_text = tok.string
                if tok_text.startswith(_KEEP_PREFIXES):
                    continue
                idx = src.find(tok_text, offsets[sl] + sc)
                if idx == -1:
                    continue
                end = src.find("\n", idx)
                if end == -1:
                    end = len(src)
                spans.append((_extend_left(src, idx), end))
                continue
            spans.append((_extend_left(src, offsets[sl] + sc), offsets[el] + ec))
    except (tokenize.TokenError, IndentationError):
        return src
    for start, end in sorted(spans, reverse=True):
        src = src[:start] + src[end:]
    return src


def strip_docstrings(src: str, rel: str) -> str:

    if rel in _KEEP_MODULE_DOCSTRING:
        return src
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    data = src.encode("utf-8")
    line_byte_offsets = _byte_line_offsets(data)
    spans = []
    def visit(node):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            d = body[0]
            start = line_byte_offsets[d.lineno] + d.col_offset
            end = line_byte_offsets[d.end_lineno] + d.end_col_offset
            is_func_or_class = isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            replace_pass = is_func_or_class and len(body) == 1
            if not replace_pass:


                while (start > 0 and data[start - 1] in (0x20, 0x09)
                       and start - 1 >= line_byte_offsets[d.lineno]):
                    start -= 1
            spans.append((start, end, replace_pass))
        for child in ast.iter_child_nodes(node):
            visit(child)
    visit(tree)
    for start, end, replace_pass in sorted(spans, reverse=True):
        if replace_pass:
            data = data[:start] + b"pass" + data[end:]
        else:
            data = data[:start] + data[end:]
    return data.decode("utf-8")


def strip_trailing_ws(src: str) -> str:

    offsets = _line_offsets(src)
    protected = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.STRING:
                continue
            sl, sc = tok.start
            el, ec = tok.end
            start = offsets[sl] + sc
            end = offsets[el] + ec
            text = src[start:end]
            if text != tok.string:
                idx = src.find(tok.string, start)
                if idx == -1:
                    continue
                start, end = idx, idx + len(tok.string)
            protected.append((start, end))
    except (tokenize.TokenError, IndentationError):
        return src
    protected.sort()
    out = []
    line_start = 0
    for line in src.splitlines(keepends=True):
        content_len = len(line.rstrip("\r\n"))
        i = content_len
        while i > 0 and line[i - 1] in " \t":
            i -= 1
        ws_start = line_start + i
        ws_end = line_start + content_len
        inside = any(s < ws_end and e > ws_start for s, e in protected)
        if inside:
            out.append(line)
        else:
            out.append(line[:i] + line[content_len:])
        line_start += len(line)
    return "".join(out)


def minify(path: Path) -> bool:
    try:
        src = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    out = strip_comments(src)
    try:
        rel = str(path.relative_to(REPO))
    except ValueError:
        rel = path.name
    out = strip_docstrings(out, rel)
    out = strip_trailing_ws(out)
    if out != src:
        path.write_text(out, encoding="utf-8")
        return True
    return False


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        paths = [Path(a) for a in argv]
    else:
        r = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO,
                           capture_output=True, text=True)
        paths = [REPO / p for p in r.stdout.splitlines() if p]
    changed = 0
    for p in paths:
        if not p.exists() or p.resolve() == Path(__file__).resolve():
            continue
        if minify(p):
            changed += 1
            try:
                label = p.relative_to(REPO)
            except ValueError:
                label = p
            print(f"  minified {label}")
    print(f"\n{changed} file(s) minified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
