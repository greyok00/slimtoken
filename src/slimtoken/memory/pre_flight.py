#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    from memory.manager import manager as _manager
except Exception:
    _manager = None



def _load_capabilities() -> Dict:

    raw = os.environ.get("SLIMTOKEN_MEMORY_MODEL_CAPABILITIES", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}



def _read_hot(profile: str) -> List[Dict]:

    if _manager is None:
        return []
    try:
        rows = _manager.get_hot_messages(profile, limit=50)
        return list(reversed(rows))
    except Exception:
        return []


def _check_cache(profile: str, prompt: str) -> Dict:

    msgs = _read_hot(profile)
    for m in msgs[-50:]:
        if m.get("role") == "user" and m.get("content") == prompt:

            try:
                idx = msgs.index(m)
                if idx + 1 < len(msgs) and msgs[idx + 1].get("role") == "assistant":
                    return {"cached": True, "response": msgs[idx + 1].get("content", "")}
            except ValueError:
                pass
    return {"cached": False, "response": None}



def classify_intent(prompt: str) -> str:

    p = prompt.lower().strip()
    if p.startswith(("run ", "execute ", "bash ", "python ", "npm ", "pip ", "git ", "docker ")):
        return "command_execution"
    if any(kw in p for kw in ["read file", "write file", "edit file",
                                "create file", "delete file", "list directory",
                                "find file", "show file"]):
        return "file_operation"
    if any(kw in p for kw in ["search", "find", "look up", "google",
                                "what is", "who is", "tell me about"]):
        return "information_retrieval"
    if any(kw in p for kw in ["remember", "save this", "store",
                                "memory", "recall", "what did i"]):
        return "memory_operation"
    if any(kw in p for kw in ["create task", "add task", "list tasks",
                                "task status", "show tasks"]):
        return "task_management"
    if any(kw in p for kw in ["hello", "hi ", "hey", "how are you",
                                "thanks", "thank you"]):
        return "conversation"
    if any(kw in p for kw in ["schedule", "cron", "every", "remind me"]):
        return "scheduling"
    if any(kw in p for kw in ["verify", "check", "test", "validate"]):
        return "verification"
    if is_ambiguous(prompt):
        return "ambiguous"
    return "llm_required"


def is_ambiguous(prompt: str) -> bool:

    p = prompt.strip()
    if not p or len(p) < 8:
        return True
    words = p.split()
    if len(words) <= 4:

        return True

    pronouns = sum(1 for w in words if w.lower() in {
        "it", "this", "that", "these", "those", "they", "them",
        "there", "here", "do", "does", "did", "fix", "make", "update",
    })
    if len(words) <= 7 and pronouns >= 1 and not any(c.isupper() for c in p):
        return True
    return False



class PreFlightResult:
    def __init__(self):
        self.passed = True
        self.blocked = False
        self.reason: Optional[str] = None
        self.cached_response: Optional[str] = None
        self.reroute_to: Optional[str] = None
        self.warnings: List[str] = []
        self.intent: str = "unknown"
        self.budget_remaining: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "reason": self.reason,
            "cached_response": self.cached_response,
            "reroute_to": self.reroute_to,
            "warnings": self.warnings,
            "intent": self.intent,
            "budget_remaining": self.budget_remaining,
        }


class PreFlightGate:
    def __init__(self, max_iterations: int = 100):
        self.max_iterations = max_iterations
        self.capabilities = _load_capabilities()
        self._iter: Dict[str, int] = {}

    def check(self, prompt: str, profile: str = "default",
              budget_remaining: Optional[float] = None) -> PreFlightResult:
        result = PreFlightResult()


        if not prompt or not prompt.strip():
            result.passed = False
            result.blocked = True
            result.reason = "Empty prompt"
            return result


        if budget_remaining is not None and budget_remaining <= 0:
            result.passed = False
            result.blocked = True
            result.reason = "Budget exhausted"
            return result
        result.budget_remaining = budget_remaining


        count = self._iter.get(profile, 0)
        if count >= self.max_iterations:
            result.passed = False
            result.blocked = True
            result.reason = f"Iteration limit ({self.max_iterations}) reached for {profile}"
            return result
        self._iter[profile] = count + 1


        cache = _check_cache(profile, prompt)
        if cache["cached"]:
            result.cached_response = cache["response"]
            result.warnings.append("Returning cached response — LLM call skipped")


        result.intent = classify_intent(prompt)

        if result.intent == "ambiguous" and not result.cached_response:
            result.warnings.append(
                "Prompt looks ambiguous — asking for clarification instead of "
                "passing to big. Rephrase with the file, function, or goal."
            )
            result.cached_response = (
                "Could you clarify what you mean?\n"
                "• Which file or component?\n"
                "• What's the symptom or expected behavior?\n"
                "• Any constraints (no restart, must keep model X, etc)?"
            )
        return result

    def reset_iterations(self, profile: str) -> None:
        self._iter.pop(profile, None)


def verify_before_llm(prompt: str, profile: str = "default",
                      budget: Optional[float] = None) -> PreFlightResult:

    return PreFlightGate().check(prompt, profile=profile, budget_remaining=budget)



def _cli(argv: List[str]) -> int:
    if not argv:
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
    if cmd == "check":
        prompt = kwargs.get("prompt") or " ".join(rest)
        profile = kwargs.get("profile", "default")
        budget = float(kwargs["budget"]) if "budget" in kwargs else None
        r = PreFlightGate().check(prompt, profile=profile, budget_remaining=budget)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if not r.blocked else 1
    if cmd == "intent":
        prompt = kwargs.get("prompt") or " ".join(rest)
        print(classify_intent(prompt))
        return 0
    if cmd == "cached":
        prompt = kwargs.get("prompt") or " ".join(rest)
        profile = kwargs.get("profile", "default")
        c = _check_cache(profile, prompt)
        print(json.dumps(c, indent=2))
        return 0 if not c["cached"] else 1
    if cmd == "smoke":
        return _smoke()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _smoke() -> int:
    g = PreFlightGate()


    r = g.check("   ")
    assert r.blocked
    print(f"  empty prompt: blocked={r.blocked}  reason={r.reason}")


    r = g.check("edit file lib/foo.py", profile="default")
    assert not r.blocked
    assert r.intent == "file_operation"
    print(f"  file-op intent: passed={r.passed}  intent={r.intent}")


    r = g.check("Run python3 --version")
    assert r.intent == "command_execution"
    print(f"  command intent: {r.intent}")


    r = g.check("hello there")
    assert r.intent == "conversation"
    print(f"  greeting intent: {r.intent}")


    r = g.check("anything", budget_remaining=0)
    assert r.blocked
    print(f"  budget=0: blocked={r.blocked}  reason={r.reason}")


    g2 = PreFlightGate(max_iterations=2)
    g2.check("a", profile="x")
    g2.check("b", profile="x")
    r = g2.check("c", profile="x")
    assert r.blocked
    print(f"  iter cap: blocked={r.blocked}  reason={r.reason}")


    g2.reset_iterations("x")
    r = g2.check("d", profile="x")
    assert not r.blocked
    print(f"  reset clears iter: passed={r.passed}")

    print("pre_flight_gate: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
