#!/usr/bin/env python3
"""context_pruner — strip low-value tokens, RAG-style retrieval, sliding-window.

Token-budget context pruning. Stdlib only, no DB deps.
Pure functions: pass in cold data + warm entries, get back a PruneResult
with token-aware truncation.

CLI:
  python3 context_pruner.py smoke
  python3 context_pruner.py prune --cold-file PATH --warm-file PATH [--query "..."]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


def estimate_tokens(text: str) -> int:
    """Rough token estimator: 4 chars per token."""
    return max(1, len(text) // 4)


# ── Low-value patterns ────────────────────────────────────────────────────
LOW_VALUE_PATTERNS = [
    # Filler phrases
    r"\b(?:I think|I believe|I feel|In my opinion|It seems|It appears|Basically|Actually|Honestly)\b",
    # Repeated punctuation
    r"[!?.]{3,}",
    # Excessive whitespace (4+ newlines stripped; 3+ normalized by collapse_whitespace)
    r"\n{4,}",
    # Trailing/leading whitespace on lines
    r"^\s+|\s+$",
]

# Metadata fields to strip from memory entries before injection
STRIP_METADATA_KEYS = {"id", "tokens_in", "tokens_out", "metadata", "platform"}


def strip_low_value(text: str) -> str:
    """Pass 1: Strip low-value tokens and patterns."""
    for pattern in LOW_VALUE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()


def collapse_whitespace(text: str) -> str:
    """Pass 2: Collapse excessive whitespace."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def truncate_verbose(text: str, max_chars: int = 500) -> str:
    """Pass 3: Truncate verbose passages, keeping first and last sentences."""
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 3:
        return text[:max_chars] + "..."
    return " ".join(sentences[:2] + ["..."]) + " " + sentences[-1]


# ── RAG-style retrieval ───────────────────────────────────────────────────
def retrieve_relevant_warm(query: str, warm_entries: List[Dict],
                           max_entries: int = 10) -> List[Dict]:
    """Pull only warm memory rows relevant to the current query.
    Uses simple keyword overlap scoring (no LLM call needed)."""
    if not warm_entries or not query:
        return warm_entries[:max_entries] if warm_entries else []
    query_words = set(query.lower().split())
    scored = []
    for entry in warm_entries:
        content = entry.get("content", "")
        if not content:
            continue
        content_words = set(content.lower().split())
        overlap = len(query_words & content_words)
        if overlap > 0:
            scored.append((overlap, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:max_entries]]


# ── Sliding-window turn-summarization ─────────────────────────────────────
def summarize_turns(warm_entries: List[Dict], window_size: int = 20) -> List[Dict]:
    """Summarize older warm entries into condensed form.

    Entries beyond the window are collapsed: consecutive user/assistant pairs
    become a single summary line. Entries within the window are kept verbatim.
    """
    if len(warm_entries) <= window_size:
        return warm_entries
    recent = warm_entries[-window_size:]
    older = warm_entries[:-window_size]
    summaries = []
    i = 0
    while i < len(older):
        entry = older[i]
        role = entry.get("role", "user")
        content = truncate_verbose(entry.get("content", ""), 200)
        if i + 1 < len(older) and older[i + 1].get("role") != role:
            next_content = truncate_verbose(older[i + 1].get("content", ""), 200)
            summaries.append({
                "role": "summary",
                "content": f"[{role}: {content[:100]}... → {older[i+1].get('role', '')}: {next_content[:100]}...]",
            })
            i += 2
        else:
            summaries.append({
                "role": "summary",
                "content": f"[{role}: {content[:150]}...]",
            })
            i += 1
    return summaries + recent


# ── Main pipeline ─────────────────────────────────────────────────────────
class PruneResult:
    def __init__(self, cold_text: str, warm_text: str, stats: Dict):
        self.cold_text = cold_text
        self.warm_text = warm_text
        self.token_count = estimate_tokens(cold_text) + estimate_tokens(warm_text)
        self.stats = stats

    def to_prompt_block(self) -> str:
        parts = []
        if self.cold_text:
            parts.append(f"<cold_memory>\n{self.cold_text}\n</cold_memory>")
        if self.warm_text:
            parts.append(f"<recent_context>\n{self.warm_text}\n</recent_context>")
        return "\n\n".join(parts)


def prune_context(cold_data: Optional[Dict], warm_entries: List[Dict],
                  query: str = "", max_tokens: int = 2000) -> PruneResult:
    """Run the full context pruning pipeline."""
    stats = {
        "cold_entries_before": 0,
        "cold_entries_after": 0,
        "warm_entries_before": len(warm_entries),
        "warm_entries_after": 0,
        "tokens_before": 0,
        "tokens_after": 0,
    }

    # --- Cold memory pruning ---
    cold_text = ""
    cold_entry_count = 0
    cold_entry_texts: List[str] = []
    if cold_data:
        stats["cold_entries_before"] = sum(len(v) for v in cold_data.values())
        for _name, entries in sorted(cold_data.items()):
            for e in entries:
                cold_entry_count += 1
                if isinstance(e, dict):
                    lines = []
                    for k, v in e.items():
                        if k in STRIP_METADATA_KEYS:
                            continue
                        if isinstance(v, str) and len(v) > 300:
                            v = v[:300] + "..."
                        lines.append(f"{k}: {v}")
                    entry_text = "\n".join(lines)
                else:
                    entry_text = str(e)[:200]
                entry_text = strip_low_value(entry_text)
                entry_text = collapse_whitespace(entry_text)
                if entry_text:
                    cold_entry_texts.append(entry_text)
        cold_text = "\n\n".join(cold_entry_texts)
        stats["cold_entries_after"] = cold_entry_count

    # --- Warm memory pruning ---
    if query:
        warm_entries = retrieve_relevant_warm(query, warm_entries)
    warm_entries = summarize_turns(warm_entries)

    pruned_warm: List[Dict] = []
    for entry in warm_entries:
        pruned = {k: v for k, v in entry.items() if k not in STRIP_METADATA_KEYS}
        content = pruned.get("content", "")
        content = strip_low_value(content)
        content = collapse_whitespace(content)
        if content:
            pruned["content"] = content
            pruned_warm.append(pruned)

    stats["warm_entries_after"] = len(pruned_warm)
    warm_text = "\n".join(
        f"{e.get('role', 'user')}: {e.get('content', '')}" for e in pruned_warm
    )

    # --- Token budget enforcement ---
    stats["tokens_before"] = estimate_tokens(cold_text) + estimate_tokens(warm_text)
    if stats["tokens_before"] > max_tokens:
        warm_tokens = estimate_tokens(warm_text)
        cold_tokens = estimate_tokens(cold_text)
        total = warm_tokens + cold_tokens
        budget_for_warm = int(max_tokens * (warm_tokens / total)) if total > 0 else max_tokens // 2
        budget_for_cold = max_tokens - budget_for_warm

        if warm_tokens > budget_for_warm:
            cumulative = 0
            keep = 0
            for entry in pruned_warm:
                entry_text = f"{entry.get('role', 'user')}: {entry.get('content', '')}"
                entry_tokens = estimate_tokens(entry_text)
                if cumulative + entry_tokens > budget_for_warm:
                    break
                cumulative += entry_tokens
                keep += 1
            keep = max(1, keep)
            truncated = len(pruned_warm) - keep
            warm_text = "\n".join(
                f"{pruned_warm[i].get('role', 'user')}: {pruned_warm[i].get('content', '')}"
                for i in range(keep)
            )
            if truncated > 0:
                warm_text += f"\n... ({truncated} more entries truncated)"

        if cold_tokens > budget_for_cold:
            cumulative = 0
            keep = 0
            for entry_text in cold_entry_texts:
                entry_tokens = estimate_tokens(entry_text)
                if cumulative + entry_tokens > budget_for_cold:
                    break
                cumulative += entry_tokens
                keep += 1
            keep = max(1, keep)
            truncated = cold_entry_count - keep
            cold_text = "\n\n".join(cold_entry_texts[:keep])
            if truncated > 0:
                cold_text += f"\n... ({truncated} more entries truncated)"

    stats["tokens_after"] = estimate_tokens(cold_text) + estimate_tokens(warm_text)
    return PruneResult(cold_text, warm_text, stats)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cli(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "smoke":
        return _smoke()
    if cmd == "prune":
        kwargs: Dict[str, str] = {}
        i = 0
        while i < len(rest):
            if rest[i].startswith("--") and i + 1 < len(rest):
                kwargs[rest[i][2:]] = rest[i + 1]
                i += 2
            else:
                i += 1
        cold_file = kwargs.get("cold-file")
        warm_file = kwargs.get("warm-file")
        query = kwargs.get("query", "")
        max_tok = int(kwargs.get("max-tokens", "2000"))
        cold_data = {}
        if cold_file and Path(cold_file).exists():
            cold_data = json.loads(Path(cold_file).read_text())
        warm_entries: List[Dict] = []
        if warm_file and Path(warm_file).exists():
            raw = json.loads(Path(warm_file).read_text())
            if isinstance(raw, dict) and "messages" in raw:
                warm_entries = raw["messages"]
            elif isinstance(raw, list):
                warm_entries = raw
        r = prune_context(cold_data, warm_entries, query=query, max_tokens=max_tok)
        print(r.to_prompt_block())
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _smoke() -> int:
    # strip_low_value
    s = strip_low_value("I think that basically this is actually fine.")
    assert "I think" not in s and "basically" not in s and "actually" not in s
    print(f"  strip_low_value: 'I think that basically this is actually fine.' → '{s}'")

    # collapse_whitespace
    s = collapse_whitespace("foo\n\n\n\n\nbar  baz")
    assert "\n\n\n" not in s and "  " not in s
    print(f"  collapse_whitespace: 'foo\\n\\n\\n\\n\\nbar  baz' → '{s}'")

    # truncate_verbose
    s = truncate_verbose("A. " + ("X" * 600) + " Z.", max_chars=100)
    assert len(s) < 200
    print(f"  truncate_verbose: 600 chars → {len(s)} chars")

    # retrieve_relevant_warm
    warm = [
        {"role": "user", "content": "Python async best practices"},
        {"role": "assistant", "content": "Use asyncio with care"},
        {"role": "user", "content": "What's the weather today"},
    ]
    r = retrieve_relevant_warm("async python", warm)
    assert len(r) >= 1 and "async" in r[0]["content"].lower()
    print(f"  retrieve_relevant_warm: query='async python' → {len(r)} hits")

    # summarize_turns
    big = [{"role": "user" if i % 2 == 0 else "assistant",
            "content": f"msg {i} " + ("x" * 50)} for i in range(30)]
    s = summarize_turns(big, window_size=10)
    assert len(s) == 10 + 10  # 10 summaries + 10 recent
    print(f"  summarize_turns: 30 → {len(s)} (10 recent + 10 summaries)")

    # prune_context integration
    cold = {
        "rules": [{"rule": "no_secrets", "value": "true"}],
        "settings": [{"key": "ctx", "value": "262144"}],
    }
    warm = [{"role": "user", "content": "foo"},
            {"role": "assistant", "content": "bar"}]
    r = prune_context(cold, warm, query="", max_tokens=1000)
    assert r.token_count < 1000
    print(f"  prune_context: {r.token_count} tokens, stats={r.stats}")

    print("context_pruner: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))