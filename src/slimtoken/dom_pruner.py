"""dom_pruner — strip irrelevant DOM before it hits the model.

DOM pruner for web-page text extraction. Stdlib only.
Pipeline:
  1. Strip non-semantic tags (script, style, svg, ...)
  2. Strip low-value sections (nav, footer, sidebar, ...)
  3. Strip non-semantic attributes (class, id, aria-*, data-*, ...)
  4. Collapse to visible text
  5. Session-aware LRU cache (clear on task boundary)

Backported from CortexAgent's ``lib/dom_pruner.py`` and wired into the
pipeline as the opt-in ``dom`` stage (``SLIMTOKEN_MINIFY_DOM=1``).

CLI:
  python3 -m slimtoken.dom_pruner prune --html "<html>...</html>" --session ID [--task ID]
  python3 -m slimtoken.dom_pruner clear-cache
  python3 -m slimtoken.dom_pruner smoke
"""
from __future__ import annotations

import json
import re
import sys
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple


# ── Session-aware LRU cache ───────────────────────────────────────────────
class DOMPruneCache:
    MAX_ENTRIES = 100

    def __init__(self):
        self._cache: "OrderedDict[str, str]" = OrderedDict()
        self._current_session: Optional[str] = None

    def _make_key(self, session_id: str, task_id: Optional[str] = None) -> str:
        return f"{session_id}::{task_id}" if task_id else session_id

    def get(self, session_id: str, task_id: Optional[str] = None) -> Optional[str]:
        key = self._make_key(session_id, task_id)
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)
        return value

    def set(self, session_id: str, pruned_dom: str, task_id: Optional[str] = None) -> None:
        key = self._make_key(session_id, task_id)
        if len(self._cache) >= self.MAX_ENTRIES:
            self._cache.popitem(last=False)
        self._cache[key] = pruned_dom
        self._cache.move_to_end(key)
        self._current_session = session_id

    def clear(self) -> None:
        self._cache.clear()
        self._current_session = None

    @property
    def current_session(self) -> Optional[str]:
        return self._current_session


_dom_cache = DOMPruneCache()


# ── Strip patterns ────────────────────────────────────────────────────────
STRIP_TAGS = {
    "script", "style", "noscript", "meta", "link", "svg",
    "path", "circle", "rect", "line", "polyline", "polygon",
    "defs", "clipPath", "mask", "use", "symbol",
}

STRIP_ATTRS = {
    "style", "class", "id", "tabindex", "role",
    "onclick", "onload", "onerror", "onmouseover", "onmouseout",
}

LOW_VALUE_SELECTORS = [
    r"<nav[^>]*>.*?</nav>",
    r"<footer[^>]*>.*?</footer>",
    r"<header[^>]*>.*?</header>",
    r"<aside[^>]*>.*?</aside>",
    # Match full element by class — backreference captures the tag name
    r"<(\w+)[^>]*class=\"[^\"]*(?:nav|menu|footer|header|sidebar|advert|cookie|modal|overlay)[^\"]*\"[^>]*>.*?</\1>",
]


def strip_tags(html: str) -> str:
    for tag in STRIP_TAGS:
        # Container form first (removes content), then self-closing, then
        # bare void/stray opening tags (e.g. <meta ...>, <link ...> with no
        # `/` and no closing tag) which the first two patterns miss.
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html,
                      flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(rf"<{tag}[^>]*/>", "", html, flags=re.IGNORECASE)
        html = re.sub(rf"<{tag}[^>]*>", "", html, flags=re.IGNORECASE)
    return html


def strip_low_value_sections(html: str) -> str:
    for pattern in LOW_VALUE_SELECTORS:
        html = re.sub(pattern, "", html, flags=re.DOTALL | re.IGNORECASE)
    return html


def strip_attributes(html: str) -> str:
    # data-* and aria-* wildcard attributes
    html = re.sub(r'\s+(?:data|aria)-[a-zA-Z_-]+="[^"]*"', "", html)
    for attr in STRIP_ATTRS:
        html = re.sub(rf'\s+{attr}="[^"]*"', "", html)
    return html


def collapse_text(html: str) -> str:
    # Block-level → newlines
    for tag in ["div", "p", "br", "li", "h[1-6]", "tr", "section", "article"]:
        html = re.sub(rf"</?{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Inline → space
    for tag in ["span", "a", "strong", "em", "b", "i", "u", "code"]:
        html = re.sub(r"</?{}[^>]*>".format(tag), " ", html, flags=re.IGNORECASE)
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&quot;", '"')
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r"[ \t]{2,}", " ", html)
    return html.strip()


MAX_HTML_SIZE = 100 * 1024 * 1024  # 100 MB


def prune_dom(raw_html: str, session_id: str, task_id: Optional[str] = None) -> str:
    if not isinstance(raw_html, str):
        raise TypeError(f"raw_html must be str, got {type(raw_html).__name__}")
    if not isinstance(session_id, str):
        raise TypeError(f"session_id must be str, got {type(session_id).__name__}")
    if task_id is not None and not isinstance(task_id, str):
        raise TypeError(f"task_id must be str or None, got {type(task_id).__name__}")
    if len(raw_html) > MAX_HTML_SIZE:
        raise ValueError(
            f"raw_html exceeds maximum size of {MAX_HTML_SIZE} bytes "
            f"({len(raw_html)} bytes)"
        )

    cached = _dom_cache.get(session_id, task_id)
    if cached is not None:
        return cached

    result = strip_tags(raw_html)
    result = strip_low_value_sections(result)
    result = strip_attributes(result)
    result = collapse_text(result)

    if len(result) > 8000:
        result = result[:4000] + "\n... [truncated] ...\n" + result[-2000:]

    _dom_cache.set(session_id, result, task_id)
    return result


def clear_dom_cache() -> None:
    _dom_cache.clear()


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cli(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "smoke":
        return _smoke()
    if cmd == "clear-cache":
        clear_dom_cache()
        print("cleared")
        return 0
    if cmd == "prune":
        kwargs: Dict[str, str] = {}
        positional: List[str] = []
        i = 0
        while i < len(rest):
            if rest[i].startswith("--") and i + 1 < len(rest):
                kwargs[rest[i][2:]] = rest[i + 1]
                i += 2
            else:
                positional.append(rest[i])
                i += 1
        html = kwargs.get("html", " ".join(positional))
        session = kwargs.get("session", "default")
        task = kwargs.get("task")
        print(prune_dom(html, session, task))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _smoke() -> int:
    # Strip script/style
    h = "<html><head><script>alert('x')</script></head><body>hi</body></html>"
    p = prune_dom(h, "smoke_session")
    assert "alert" not in p and "hi" in p
    print(f"  strip script: 'alert' removed, 'hi' kept")

    # Strip nav/footer
    h = "<html><body><nav>menu</nav><main>content</main><footer>copy</footer></body></html>"
    p = prune_dom(h, "smoke_session_2")
    assert "menu" not in p and "content" in p and "copy" not in p
    print(f"  strip nav/footer: 'menu'/'copy' removed, 'content' kept")

    # Strip attributes (class, id, data-*)
    h = '<html><body><div class="foo" id="bar" data-x="y">hi</div></body></html>'
    p = prune_dom(h, "smoke_session_3")
    assert "class=" not in p and "id=" not in p and "data-" not in p
    print(f"  strip attrs: class/id/data-* removed")

    # Cache hit (same session)
    h2 = "<html><body>different</body></html>"
    p1 = prune_dom(h2, "cached_session")
    p2 = prune_dom(h2, "cached_session")
    assert p1 == p2
    # Verify cache is hit by mutating the cache directly
    cache = _dom_cache._cache
    cached = [k for k in cache if k.startswith("cached_session")]
    assert len(cached) == 1
    print(f"  cache: same session → 1 entry, repeated call returns cached")

    # Clear cache
    clear_dom_cache()
    assert len(_dom_cache._cache) == 0
    print(f"  clear_cache: 0 entries after clear")

    # Type errors
    try:
        prune_dom(123, "s")  # type: ignore
        assert False, "expected TypeError"
    except TypeError:
        print(f"  type check: TypeError on non-str html")

    print("dom_pruner: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
