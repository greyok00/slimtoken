#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set




from .engine import HOT_DIR, COLD_DIR, atomic_append, write_cold

SEEN_FILE = Path.home() / ".cortexllm" / "state" / "distiller_seen.json"



KNOWLEDGE_PATTERNS = {
    "configuration": [
        r"(?:api|endpoint|base)\s*[=:]\s*[^\"'\s]+",
        r"(?:port|timeout|interval|limit)\s*[=:]\s*\d+",
        r"config(?:uration)?\s*(?:file|path|dir)",
        r"(?:host|url|uri)\s*[=:]\s*[^\"'\s]+",
        r"(?:db|database|redis|mqtt)\s*(?:url|path|host)",
    ],
    "error_solution": [
        r"(?:error|fix|solution|resolve|workaround)",
        r"(?:failed|crash|bug|issue)\s*(?:because|due to|caused by)",
        r"(?:try|use|run|set|change|update)\s+(?:this|that|it|the)",
        r"exit\s+status\s+\d+|exit\s+code\s+\d+",
        r"(?:permission denied|not found|no such file|connection refused)",
        r"(?:timeout|deadline exceeded|context deadline)",
    ],
    "workflow": [
        r"(?:step|stage|phase)\s*\d+",
        r"(?:first|then|next|finally|after that)",
        r"(?:workflow|process|pipeline|procedure)",
        r"(?:build|deploy|release|migrate)\s+(?:step|process|script)",
        r"(?:install|setup|configure|initialize)\s+",
    ],
    "preference": [
        r"(?:prefer|like|want|need|require)\s+(?:to|using|the)",
        r"(?:always|never|only|must|should)\s+(?:use|do|run|set)",
        r"(?:language|format|style|mode)\s*[=:]\s*[^\"']?\w+",
        r"(?:default|primary|main|preferred)\s+(?:model|provider|tool|browser)",
    ],
    "reference": [
        r"(?:is|are|means|refers to|stands for)\s+(?:a|an|the)",
        r"(?:known as|called|named|titled)",
        r"(?:version|release|build)\s*[\d.]+",
        r"(?:Module|Assignment|Project|Course)\s+\d+",
    ],
}

LOW_VALUE_PATTERNS = [
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|great)\s*$",
    r"^(?:how are you|what's up|good morning|good afternoon)\s*$",
    r"^(?:lol|lmao|rofl|haha|nice|awesome|cool)\s*$",
    r"^\s*$",
    r"^Token usage:.*$",
]



def _load_seen_facts() -> Set[str]:
    try:
        if SEEN_FILE.exists():
            return set(json.loads(SEEN_FILE.read_text()))
    except Exception:
        pass
    return set()


def _save_seen_facts(seen: Set[str]) -> None:
    try:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        facts = list(seen)[-10000:]
        SEEN_FILE.write_text(json.dumps(facts))
    except Exception:
        pass



def _read_hot_entries(platform: Optional[str] = None) -> List[Dict]:

    entries = []
    if not HOT_DIR.exists():
        return entries
    if platform:
        files = [HOT_DIR / f"{platform}.jsonl"]
    else:
        files = list(HOT_DIR.glob("*.jsonl"))
    for path in files:
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    content = entry.get("content", "")
                    if not content:
                        continue
                    entries.append({
                        "content": content,
                        "role": entry.get("role", "user"),
                        "timestamp": entry.get("timestamp"),
                        "platform": entry.get("platform", path.stem),
                    })
        except OSError:
            continue
    return entries





_read_warm_entries = _read_hot_entries



def _write_cold_fact(category: str, fact: Dict, profile: str = "shared") -> bool:

    try:
        knowledge = {
            "description": fact.get("description", ""),
            "confidence": fact.get("confidence", 0.5),
            "tags": fact.get("tags", [category]),
            "fact": fact.get("fact", ""),
            "extracted_at": datetime.now().isoformat(),
            "source": f"distiller:{profile}",
        }
        write_cold(category, knowledge, source=f"distiller:{profile}")
        return True
    except Exception:
        return False



class ColdDistiller:
    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence
        self._seen_facts: Set[str] = _load_seen_facts()

    def run(self, profile: Optional[str] = None) -> Dict:
        stats = {
            "scanned": 0,
            "extracted": 0,
            "skipped_low_value": 0,
            "skipped_duplicate": 0,
            "errors": 0,
            "categories": {},
        }

        rows = _read_hot_entries(platform=profile)
        stats["scanned"] = len(rows)

        for row in rows:
            content = row.get("content", "")
            if not content or len(content) < 20:
                continue
            if self._is_low_value(content):
                stats["skipped_low_value"] += 1
                continue

            facts = self._extract_facts(content, row)
            for fact in facts:
                fact_key = f"{fact['category']}:{fact['fact'][:100]}"
                if fact_key in self._seen_facts:
                    stats["skipped_duplicate"] += 1
                    continue
                if fact["confidence"] < self.min_confidence:
                    continue
                target_profile = profile or row.get("profile", "shared")
                ok = _write_cold_fact(
                    fact["category"],
                    {
                        "description": fact["fact"],
                        "fact": fact["fact"],
                        "tags": fact["tags"],
                        "confidence": fact["confidence"],
                        "profile": row.get("profile", "shared"),
                    },
                    profile=target_profile,
                )
                if ok:
                    self._seen_facts.add(fact_key)
                    stats["extracted"] += 1
                    stats["categories"][fact["category"]] = \
                        stats["categories"].get(fact["category"], 0) + 1
                else:
                    stats["errors"] += 1

        _save_seen_facts(self._seen_facts)
        return stats

    def _extract_facts(self, content: str, row: Dict) -> List[Dict]:
        facts = []
        for category, patterns in KNOWLEDGE_PATTERNS.items():
            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches[:3]:
                    fact_text = match.strip()
                    if len(fact_text) < 5:
                        continue
                    if len(fact_text) < 30:
                        idx = content.lower().find(fact_text.lower())
                        if idx >= 0:
                            start = max(0, idx - 40)
                            end = min(len(content), idx + len(fact_text) + 80)
                            context = content[start:end].strip()
                            if len(context) > len(fact_text):
                                fact_text = context
                    confidence = self._calculate_confidence(category, fact_text, content)
                    facts.append({
                        "category": category,
                        "fact": fact_text,
                        "confidence": confidence,
                        "tags": self._generate_tags(category, row),
                        "profile": row.get("profile", "shared"),
                    })
        return facts

    def _calculate_confidence(self, category: str, match: str, content: str) -> float:
        base = 0.5
        if len(match) > 50:
            base += 0.2
        if len(match) > 100:
            base += 0.1
        if category == "configuration":
            base += 0.2
        elif category == "error_solution":
            base += 0.1
        elif category == "reference":
            base += 0.1
        return min(base, 1.0)

    def _generate_tags(self, category: str, row: Dict) -> List[str]:
        tags = [category]
        profile = row.get("profile", "")
        if profile:
            tags.append(f"source:{profile}")
        return tags

    def _is_low_value(self, content: str) -> bool:
        content_stripped = content.strip().lower()
        for pattern in LOW_VALUE_PATTERNS:
            if re.match(pattern, content_stripped):
                return True
        return False



def _print_stats(stats: Dict) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
          f"Scanned: {stats['scanned']} | "
          f"Extracted: {stats['extracted']} | "
          f"Skipped (low value): {stats['skipped_low_value']} | "
          f"Skipped (duplicate): {stats['skipped_duplicate']} | "
          f"Errors: {stats['errors']}")
    if stats["categories"]:
        cats = ", ".join(f"{k}: {v}" for k, v in stats["categories"].items())
        print(f"  Categories: {cats}")


def _cli(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    kwargs: Dict[str, str] = {}
    i = 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            kwargs[rest[i][2:]] = rest[i + 1]
            i += 2
        else:
            i += 1

    if cmd == "smoke":
        return _smoke()
    if cmd == "run":
        profile = kwargs.get("profile")
        min_conf = float(kwargs.get("min-confidence", "0.5"))
        d = ColdDistiller(min_confidence=min_conf)
        stats = d.run(profile=profile)
        _print_stats(stats)
        return 0
    if cmd == "daemon":
        interval = int(kwargs.get("interval", "1800"))
        profile = kwargs.get("profile")
        print(f"cold_distiller daemon started (interval: {interval}s)")
        d = ColdDistiller()
        while True:
            stats = d.run(profile=profile)
            _print_stats(stats)
            time.sleep(interval)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _smoke() -> int:
    d = ColdDistiller(min_confidence=0.0)

    assert d._is_low_value("hello")
    assert d._is_low_value("thanks")
    assert not d._is_low_value("The API endpoint is https://api.example.com/v1")
    print("  low-value filter: greetings skipped, factual content kept")

    content = "The API endpoint is https://api.example.com/v1. Then run npm install after that."
    facts = d._extract_facts(content, {"profile": "test"})
    cats = {f["category"] for f in facts}
    assert "configuration" in cats or "workflow" in cats
    print(f"  extract_facts: {len(facts)} facts from multi-pattern content")

    base = d._calculate_confidence("reference", "short", "x")
    long_conf = d._calculate_confidence("reference", "x" * 150, "x" * 200)
    assert long_conf > base
    print(f"  confidence: short={base:.2f}, long={long_conf:.2f}")

    stats = d.run()
    assert "scanned" in stats
    print(f"  run(): scanned={stats['scanned']} extracted={stats['extracted']}")

    _save_seen_facts({"a", "b"})
    loaded = _load_seen_facts()
    assert "a" in loaded
    print("  seen_facts: persisted then reloaded")

    print("cold_distiller: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))


def main() -> int:

    return _cli(sys.argv[1:])
