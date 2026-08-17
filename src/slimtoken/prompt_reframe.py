"""prompt_reframe — generic CPU-only prompt rewriter.

A small, self-contained toolkit for tightening natural-language prompts before
they reach a model. All stages are pure CPU (no LLM roundtrip), deterministic,
and safe to run anywhere: a script, a CLI, an MCP server, or a web service.

Stages (each one independently callable):

    classify_domain(prompt)
        Keyword match into one of: business, professional, osint,
        cybersecurity, code, general. Stable classification; falls back
        to 'general' if nothing matches.

    reframe_prompt(prompt)
        Strip conversational filler ("can you basically just tell me…"),
        drop fragment patterns ("...", "the the"), dedupe sentences
        (case-insensitive), normalize whitespace, capitalize the first
        letter, ensure terminal punctuation. Lossless on intent — every
        claim survives; only filler and duplicates are removed.

    shrink_prompt(prompt, max_tokens=80, mode='balanced')
        Rank sentences by a TextRank-lite score (overlap with the whole
        prompt + length), keep the top-N until the word budget is met,
        re-emit in original order. Modes:
          - 'aggressive' ≈ 20 words (~ caveman)
          - 'balanced'   ≈ 50 words (~ tight business prose)
          - 'preserve'   ≈ 150 words (~ light cleanup)
        The output is *constructed from sentences that already appear in
        the input* — there is no semantic rephrasing, so intent cannot
        drift. Use it when you want a known-good CPU fallback.

    minify_prompt(prompt)
        Character-level squeeze: collapse whitespace, drop redundant
        punctuation runs. Pure cosmetic.

    build_system(domain, role='generalist', style='terse', rules=())
        Compose a tight system prompt from a small, fixed schema.
        Intentionally short — a few declarative sentences that don't
        waste the model's context window.

The module is dependency-free (Python 3.10+ stdlib only). It does not
import any model client, agent runtime, or upstream SDK. If you ship a
product that embeds a rewriter, this is the layer that should live in
the model-agnostic core.

Why split this out from the rest of slimtoken? The proxy already minifies
the *request body* (tools, system, messages, dedup, distill). This module
rewrites the *natural-language intent* of a single user prompt — a
different problem, used at a different point in the pipeline. Keeping
them separate lets you compose them: proxy-minify a long multi-turn
request, then reframe each new user prompt as it arrives.

Usage:
    from slimtoken.prompt_reframe import (
        classify_domain, reframe_prompt, shrink_prompt,
        minify_prompt, build_system,
    )

    domain = classify_domain("quarterly revenue forecast vs plan")
    tight  = shrink_prompt(raw_user_msg, mode='balanced')
    system = build_system(domain, role='generalist', style='terse')
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Tuple

__all__ = [
    "classify_domain",
    "reframe_prompt",
    "shrink_prompt",
    "minify_prompt",
    "build_system",
    "shrink_modes",
]


# ── Domain Classification ──────────────────────────────────────────────────
DOMAIN_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "cybersecurity": (
        "security", "malware", "virus", "trojan", "ransomware", "phishing",
        "breach", "incident", "forensics", "exploit", "vulnerability",
        "attack", "defense", "firewall", "intrusion", "threat", "ioc",
        "indicator", "compromise", "anomaly", "suspicious",
    ),
    "osint": (
        "osint", "open source", "intelligence", "investigate", "search",
        "find", "locate", "track", "monitor", "watch", "surveillance",
        "reconnaissance", "scout", "probe", "scan", "enumeration",
    ),
    "business": (
        "business", "company", "market", "revenue", "profit", "cost",
        "budget", "forecast", "strategy", "plan", "growth", "investment",
        "roi", "kpi", "metrics", "analytics", "dashboard", "report",
    ),
    "code": (
        "code", "function", "class", "method", "compile", "import",
        "module", "refactor", "debug", "bug", "fix", "patch", "git",
        "merge", "commit", "branch", "test", "lint", "type",
    ),
    "professional": (
        "professional", "report", "analysis", "research", "study",
        "review", "assessment", "evaluation", "audit", "compliance",
        "regulation", "policy", "procedure", "guideline", "standard",
    ),
}


def classify_domain(prompt: str) -> str:
    """Keyword match into a domain. Stable; falls back to 'general'.

    The score per domain is the count of its keywords that appear
    (case-insensitive substring) in the prompt. Ties resolve by the
    order the domains are defined in :data:`DOMAIN_KEYWORDS`.
    """
    if not prompt:
        return "general"
    lower = prompt.lower()
    scores: Dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in lower)
        if score > 0:
            scores[domain] = score
    if not scores:
        return "general"
    best = max(scores.values())
    for domain, score in scores.items():
        if score == best:
            return domain
    return "general"


# ── Filler / Stopword Sets ─────────────────────────────────────────────────
_FILLER_PHRASES: Tuple[str, ...] = (
    "i want to know", "i want", "i'd like", "i would like",
    "can you tell me", "can you", "could you",
    "i need you to", "i need", "help me with", "help me",
    "please", "thanks", "thank you",
    "a lot of", "kind of", "sort of", "type of",
    "basically", "essentially", "literally", "actually",
    "just", "really", "very", "quite", "pretty",
    "in order to", "for the purpose of",
    "as well as", "in addition to",
    "due to the fact that", "in spite of the fact that",
    "at this point in time", "at the present time",
    "i was wondering", "i'm wondering",
    "do you think", "would you be able",
    "it's important to note", "it should be noted",
)

_FRAGMENT_PATTERNS: Tuple[str, ...] = (
    r"\.{3,}",            # "..." "...."
    r"\b(\w+)\s+\1\b",    # "the the"
    r"\s+,",              # " ,"
    r",\s*,",             # ", ,"
    r"\s+\.",             # " ."
    r"\?{2,}", r"!{2,}",
)


# ── Stage: reframe (strip filler + dedupe + normalize) ─────────────────────
def reframe_prompt(prompt: str) -> str:
    """Strip filler, drop fragments, dedupe sentences, normalize whitespace.

    The transform is *lossless on actionable content* — every factual
    claim survives; only the conversational scaffolding is removed.
    Use it whenever a prompt has too many pleasantries, false starts,
    or repeated lines.
    """
    if not prompt:
        return prompt
    s = prompt

    # 1. Drop filler phrases (case-insensitive, word-boundary aware)
    for phrase in _FILLER_PHRASES:
        s = re.sub(r"\b" + re.escape(phrase) + r"\b", "", s,
                   flags=re.IGNORECASE)

    # 2. Drop fragment patterns
    for pat in _FRAGMENT_PATTERNS:
        s = re.sub(pat, " ", s)

    # 3. Split into sentences, dedupe (case-insensitive, punctuation-normalized)
    raw_sents = re.split(r"(?<=[.!?])\s+|\n+", s)
    seen: set = set()
    kept: List[str] = []
    for sent in raw_sents:
        norm = re.sub(r"[^a-z0-9 ]", " ", sent.lower()).strip()
        norm = re.sub(r"\s+", " ", norm)
        if not norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        kept.append(sent.strip())

    # 4. Re-join, normalize whitespace
    out = " ".join(kept)
    out = re.sub(r"\s+", " ", out).strip()

    # 5. Capitalize first letter, ensure terminal punctuation
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    if out and out[-1] not in ".!?":
        out += "."
    return out


# ── Stage: shrink (TextRank-lite sentence rank) ────────────────────────────
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _rank_score(sent: str, query_words: set) -> int:
    """TextRank-lite: words shared with the prompt + sentence length.

    This is a CPU fallback for environments that can't use a small LLM
    for paraphrasing. It's *deterministic* and *intent-preserving* — the
    output is built from sentences that already appear in the input, in
    the user's own words.
    """
    words = _WORD_RE.findall(sent.lower())
    overlap = sum(2 for w in words if w in query_words)
    return overlap + len(words)


shrink_modes: Dict[str, int] = {
    "aggressive": 20,
    "balanced": 50,
    "preserve": 150,
}


def shrink_prompt(prompt: str, max_tokens: Optional[int] = None,
                  mode: str = "balanced") -> str:
    """Rank sentences by relevance + length and keep the top-N.

    Args:
        prompt: the input prompt.
        max_tokens: target word budget. If ``None`` (default), it is
            resolved from ``mode`` — 'aggressive' (~20), 'balanced'
            (~50), 'preserve' (~150). Pass an explicit int to override.
        mode: budget mode; used only when ``max_tokens`` is ``None``.

    Returns:
        A shorter prompt made from sentences that already exist in the
        input — no LLM, no semantic drift, no hallucinated details.
        Falls back to the reframed text if ranking yields nothing.
    """
    if not prompt:
        return prompt
    if max_tokens is None or max_tokens <= 0:
        max_tokens = shrink_modes.get(mode, 50)

    # First-pass reframe (cheap; frequently already short-circuits the work)
    candidates = _split_sentences(reframe_prompt(prompt))
    if not candidates:
        candidates = _split_sentences(prompt)
    if not candidates:
        return prompt

    query_words = {w for w in _WORD_RE.findall(prompt.lower()) if len(w) > 3}

    # Rank each sentence; preserve original order when scores tie
    scored = [(i, _rank_score(s, query_words), s)
              for i, s in enumerate(candidates)]
    order = {id(s): i for i, (_, _, s) in enumerate(scored)}

    # Sort by score desc, then by original position asc (stable)
    ranked = sorted(scored, key=lambda r: (-r[1], r[0]))

    kept: List[str] = []
    word_count = 0
    for _idx, _score, sent in ranked:
        sw = len(sent.split())
        if word_count + sw > max_tokens and kept:
            break
        kept.append(sent)
        word_count += sw

    if not kept:
        # No sentence fit the budget alone; return the highest-scoring one
        return ranked[0][2]

    # Re-emit kept sentences in original document order so the prose flows
    kept.sort(key=lambda s: order.get(id(s), 0))
    out = " ".join(kept).strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out or prompt


# ── Stage: minify (character-level squeeze) ────────────────────────────────
def minify_prompt(prompt: str) -> str:
    """Collapse whitespace + drop redundant punctuation runs. Cosmetic."""
    if not prompt:
        return prompt
    s = re.sub(r"\s+", " ", prompt).strip()
    s = re.sub(r"[,;:!?]{2,}", "", s)
    return s


# ── Stage: build_system (tight declarative system prompt) ──────────────────
_DOMAIN_HINTS: Dict[str, str] = {
    "business":       "Structured. Metrics → trends → recommendation → risk. No hype.",
    "professional":   "Numbered. Evidence → analysis → next step.",
    "osint":          "Findings → sources (with confidence) → timeline → next lead.",
    "cybersecurity":  "Threat → IOCs → mitigation → prevention.",
    "code":           "Diff first, then plain explanation. Always include a verification step.",
    "general":        "Plain language, no filler, lead with the answer.",
}


def build_system(domain: str, role: str = "generalist",
                 style: str = "terse",
                 rules: Optional[Tuple[str, ...]] = None) -> str:
    """Compose a tight system prompt from a small, fixed schema.

    Intentionally short — every clause pulls its weight, nothing decorative.
    Defaults match what business and developer users tend to prefer; pass
    a custom ``rules`` tuple to override.

    Args:
        domain: result of :func:`classify_domain` (or any custom label).
        role: short role label, e.g. 'generalist', 'planner', 'auditor'.
        style: short style label, e.g. 'terse', 'numbered', 'evidence'.
        rules: optional tuple of explicit rules; first 6 are included.

    Returns:
        A single-line system prompt suitable for the ``system`` field of a
        chat-completions or Anthropic ``messages`` request.
    """
    hint = _DOMAIN_HINTS.get(domain, _DOMAIN_HINTS["general"])
    parts = [
        f"Role: {role}.",
        f"Style: {style}.",
        f"Domain ({domain}): {hint}",
    ]
    if rules:
        parts.append("Rules: " + "; ".join(rules[:6]))
    parts.append("Output: lead with the answer or action. No filler.")
    parts.append("Format: tables / lists when they shorten. "
                 "No code blocks unless asked. No thinking preamble.")
    return " ".join(parts)


# ── Convenience: full pipeline as one call ─────────────────────────────────
def frame_prompt(prompt: str, *, system_prompt: str = "",
                 max_tokens: Optional[int] = None,
                 mode: str = "balanced",
                 role: str = "generalist",
                 style: str = "terse",
                 rules: Optional[Tuple[str, ...]] = None
                 ) -> Tuple[str, str, str]:
    """Run the full generic pipeline on a single prompt.

    Stages: classify → reframe → shrink → minify → build_system.

    Returns ``(reframed_prompt, system_prompt, domain)``. ``system_prompt``
    is the composed system if ``system_prompt`` arg was empty; otherwise
    it is ``<user-provided>\\n\\n<composed>`` so the model's existing
    instructions stay in force.

    No LLM calls; deterministic.
    """
    if not prompt:
        return prompt, system_prompt, "general"

    domain = classify_domain(prompt)
    reframed = reframe_prompt(prompt)
    if max_tokens is not None:
        shrunk = shrink_prompt(reframed, max_tokens=max_tokens, mode=mode)
    else:
        shrunk = shrink_prompt(reframed, mode=mode)
    tight = minify_prompt(shrunk)

    composed = build_system(domain, role=role, style=style, rules=rules)
    if system_prompt:
        final_system = system_prompt + "\n\n" + composed
    else:
        final_system = composed
    return tight, final_system, domain


# ── CLI ─────────────────────────────────────────────────────────────────────
def _cli(argv: List[str]) -> int:  # pragma: no cover
    import json
    import sys

    if len(argv) >= 2 and argv[1] == "smoke":
        tests = [
            "What is the capital of France?",
            ("Investigate the security posture of this server "
             "and check for any IOCs"),
            ("Help me with my homework please can you "
             "just basically tell me what is the answer "
             "really kind of like basically"),
        ]
        for text in tests:
            refr, sysp, dom = frame_prompt(text)
            print(f"\n  IN:  {text[:80]!r}")
            print(f"  DOM: {dom}")
            print(f"  OUT: {refr}")
            print(f"  SYS: {sysp[:140]}...")
        return 0

    if len(argv) >= 2 and argv[1] == "json":
        text = argv[2] if len(argv) >= 3 else ""
        refr, sysp, dom = frame_prompt(text)
        sys.stdout.write(json.dumps({
            "domain": dom, "reframed": refr, "system": sysp,
        }))
        sys.stdout.write("\n")
        return 0

    text = " ".join(argv[1:]) if len(argv) > 1 else ""
    if not text:
        print("usage: python -m slimtoken.prompt_reframe <text>", file=sys.stderr)
        return 2
    refr, sysp, dom = frame_prompt(text)
    print(f"Domain: {dom}")
    print(f"Reframed: {refr}")
    print(f"System:\n  {sysp}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli(__import__("sys").argv))
