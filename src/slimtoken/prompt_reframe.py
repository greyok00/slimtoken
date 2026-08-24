
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
    r"\.{3,}",
    r"\b(\w+)\s+\1\b",
    r"\s+,",
    r",\s*,",
    r"\s+\.",
    r"\?{2,}", r"!{2,}",
)



def reframe_prompt(prompt: str) -> str:

    if not prompt:
        return prompt
    s = prompt


    for phrase in _FILLER_PHRASES:
        s = re.sub(r"\b" + re.escape(phrase) + r"\b", "", s,
                   flags=re.IGNORECASE)


    for pat in _FRAGMENT_PATTERNS:
        s = re.sub(pat, " ", s)


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


    out = " ".join(kept)
    out = re.sub(r"\s+", " ", out).strip()


    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    if out and out[-1] not in ".!?":
        out += "."
    return out



_WORD_RE = re.compile(r"[a-z0-9]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _rank_score(sent: str, query_words: set) -> int:

    words = _WORD_RE.findall(sent.lower())
    overlap = sum(2 for w in words if w in query_words)
    return overlap + len(words)





_IMPERATIVE_PREFIXES = (
    "plan", "check", "scan", "audit", "verify", "ensure", "make", "create",
    "write", "build", "add", "fix", "update", "change", "remove", "delete",
    "run", "execute", "start", "stop", "test", "review", "find", "locate",
    "compare", "analyze", "investigate", "search", "list", "show", "give",
    "tell", "explain", "summarize", "generate", "implement", "refactor",
    "debug", "convert", "translate", "install", "configure", "set", "reset",
    "don't", "dont", "do not", "never", "always", "must", "please",
)
_CONSTRAINT_MARKERS = (
    "must", "ensure", "keep", "do not", "don't", "dont", "never", "always",
    "must not", "require", "required", "needs to", "has to", "unless",
    "except", "preserve", "retain", "verbatim", "exactly", "critical",
)


def _is_imperative_or_constraint(sent: str) -> bool:

    low = sent.lower().strip()
    if any(low.startswith(p) for p in _IMPERATIVE_PREFIXES):
        return True
    return any(m in low for m in _CONSTRAINT_MARKERS)


shrink_modes: Dict[str, int] = {
    "aggressive": 20,
    "balanced": 50,
    "preserve": 150,
}


def shrink_prompt(prompt: str, max_tokens: Optional[int] = None,
                  mode: str = "balanced") -> str:

    if not prompt:
        return prompt
    if max_tokens is None or max_tokens <= 0:
        max_tokens = shrink_modes.get(mode, 50)


    candidates = _split_sentences(reframe_prompt(prompt))
    if not candidates:
        candidates = _split_sentences(prompt)
    if not candidates:
        return prompt
    if len(candidates) == 1:
        return candidates[0]



    if sum(len(s.split()) for s in candidates) <= max_tokens:
        out = " ".join(candidates).strip()
        return (out if out[-1] in ".!?" else out + ".") or prompt

    query_words = {w for w in _WORD_RE.findall(prompt.lower()) if len(w) > 3}
    order = {id(s): i for i, s in enumerate(candidates)}


    first = candidates[0]
    kept = [first]
    word_count = len(first.split())
    rest = candidates[1:]


    imperative = [s for s in rest if _is_imperative_or_constraint(s)]
    imp_ids = {id(s) for s in imperative}
    for s in imperative:
        sw = len(s.split())
        if word_count + sw > max_tokens:
            break
        kept.append(s)
        word_count += sw


    scored = [(i, _rank_score(s, query_words), s)
              for i, s in enumerate(rest) if id(s) not in imp_ids]
    scored.sort(key=lambda r: (-r[1], r[0]))
    for _idx, _score, sent in scored:
        sw = len(sent.split())
        if word_count + sw > max_tokens:
            break
        kept.append(sent)
        word_count += sw


    kept.sort(key=lambda s: order.get(id(s), 0))
    out = " ".join(kept).strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out or prompt



def minify_prompt(prompt: str) -> str:

    if not prompt:
        return prompt
    s = re.sub(r"\s+", " ", prompt).strip()
    s = re.sub(r"[,;:!?]{2,}", "", s)
    return s



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



def frame_prompt(prompt: str, *, system_prompt: str = "",
                 max_tokens: Optional[int] = None,
                 mode: str = "balanced",
                 role: str = "generalist",
                 style: str = "terse",
                 rules: Optional[Tuple[str, ...]] = None
                 ) -> Tuple[str, str, str]:

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
