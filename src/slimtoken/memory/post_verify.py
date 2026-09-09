#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from typing import Dict, List, Optional



BANNED_PATTERNS: List[str] = [

    r"sk-[a-zA-Z0-9]{20,}",
    r"sk-ant-[a-zA-Z0-9_-]{20,}",
    r"sk-or-[a-zA-Z0-9_-]{20,}",
    r"gho_[a-zA-Z0-9]{20,}",
    r"ghp_[a-zA-Z0-9]{20,}",
    r"github_pat_[a-zA-Z0-9_]{20,}",
    r"xai-[a-zA-Z0-9]{20,}",
    r"api[-_]?key[\"']?\s*[:=]\s*[\"'][a-zA-Z0-9]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}",

    r"password\s*[:=]\s*[\"'][^\"']{4,}[\"']",
    r"secret\s*[:=]\s*[\"'][^\"']{4,}[\"']",
    r"bearer\s+[a-zA-Z0-9_-]{20,}",

    r"\b\d{3}-\d{2}-\d{4}\b",
    r"\b\d{16}\b",
]


class PostVerifyResult:
    def __init__(self):
        self.passed = True
        self.warnings: List[str] = []
        self.reason: Optional[str] = None
        self.retry_feedback: Optional[str] = None
        self.blocked: bool = False

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "blocked": self.blocked,
            "warnings": self.warnings,
            "reason": self.reason,
            "retry_feedback": self.retry_feedback,
        }



def _check_content_safety(response: str) -> Dict:

    matches = []
    for pattern in BANNED_PATTERNS:
        m = re.search(pattern, response, re.IGNORECASE)
        if m:
            matches.append(m.group()[:40])
    if matches:
        return {
            "blocked": True,
            "reason": f"Response contains potential secret/credential: {matches[0]}…",
            "matches": matches,
        }
    return {"blocked": False, "reason": None, "matches": []}



def _validate_json(response: str, required_fields: Optional[List[str]] = None,
                   schema: Optional[Dict] = None) -> Dict:
    json_str = _extract_json(response)
    if not json_str:
        return {"valid": False, "reason": "No JSON found in response",
                "warnings": []}
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"valid": False, "reason": f"Invalid JSON: {e}", "warnings": []}
    warnings: List[str] = []
    if required_fields:
        missing = [f for f in required_fields if f not in parsed]
        if missing:
            return {"valid": False,
                    "reason": f"Missing required fields: {', '.join(missing)}",
                    "warnings": [f"Add fields: {', '.join(missing)}"]}
    if schema and isinstance(parsed, dict):
        for key, expected_type in schema.items():
            if key in parsed:
                actual = type(parsed[key]).__name__
                if expected_type == "array" and not isinstance(parsed[key], list):
                    return {"valid": False, "reason": f"Field '{key}' should be array"}
                elif expected_type == "object" and not isinstance(parsed[key], dict):
                    return {"valid": False, "reason": f"Field '{key}' should be object"}
                elif expected_type == "string" and not isinstance(parsed[key], str):
                    return {"valid": False, "reason": f"Field '{key}' should be string"}
                elif expected_type == "number" and not isinstance(parsed[key], (int, float)):
                    return {"valid": False, "reason": f"Field '{key}' should be number"}
    return {"valid": True, "warnings": warnings}


def _extract_json(text: str) -> Optional[str]:


    for block in re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        block = block.strip()
        if block.startswith(("{", "[")):
            return block

    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    return m.group(1) if m else None


def _validate_code(response: str) -> Dict:

    blocks = re.findall(r"```(\w+)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if not blocks:
        return {"valid": True, "warnings": ["No code blocks found in response"]}
    for lang, code in blocks:
        code = code.strip()
        if not code:
            continue
        if not _balanced_brackets(code):
            return {"valid": False, "reason": f"Unbalanced brackets in {lang or 'code'} block",
                    "warnings": []}
        if lang == "json":
            try:
                json.loads(code)
            except json.JSONDecodeError as e:
                return {"valid": False, "reason": f"JSON syntax error in code block: {e}",
                        "warnings": []}
    return {"valid": True, "warnings": []}


def _validate_markdown(response: str) -> Dict:
    warnings: List[str] = []
    fences = response.count("```")
    if fences % 2 != 0:
        warnings.append("Unclosed code fence")
    broken = re.findall(r"\[([^\]]*)\]\((?!https?://|#|/)([^)]*)\)", response)
    for text, url in broken:
        if not url or url.isspace():
            warnings.append(f"Broken link: [{text}]()")
    return {"valid": True, "warnings": warnings}


def _check_structure(response: str) -> Dict:

    stripped = re.sub(r"```.*?```", "", response, flags=re.DOTALL)
    if not _balanced_brackets(stripped, check_parens=True):
        return {"valid": False, "reason": "Unbalanced parentheses or brackets",
                "warnings": []}
    return {"valid": True, "warnings": []}


def _balanced_brackets(text: str, check_parens: bool = False) -> bool:
    stack = []
    pairs = {"{": "}", "[": "]"}
    if check_parens:
        pairs["("] = ")"
    for ch in text:
        if ch in pairs:
            stack.append(ch)
        elif ch in pairs.values():
            if not stack or pairs[stack.pop()] != ch:
                return False
    return len(stack) == 0



class PostResponseVerifier:
    def __init__(self):
        pass

    def verify(self, response: str, expected_format: Optional[str] = None,
               required_fields: Optional[List[str]] = None,
               schema: Optional[Dict] = None) -> PostVerifyResult:
        result = PostVerifyResult()

        if not response or not response.strip():
            result.passed = False
            result.reason = "Empty response"
            result.retry_feedback = "Response was empty. Please provide a non-empty answer."
            return result


        safety = _check_content_safety(response)
        if safety["blocked"]:
            result.passed = False
            result.blocked = True
            result.reason = safety["reason"]
            result.warnings.append(f"Content safety: {safety['reason']}")
            result.retry_feedback = (
                f"BLOCKED: {safety['reason']} "
                f"Please remove the credential and rephrase — never include secrets in output."
            )
            return result


        if expected_format == "json":
            fmt = _validate_json(response, required_fields, schema)
        elif expected_format == "code":
            fmt = _validate_code(response)
        elif expected_format == "markdown":
            fmt = _validate_markdown(response)
        else:
            fmt = {"valid": True, "warnings": []}

        if not fmt["valid"]:
            result.passed = False
            result.reason = fmt["reason"]
            result.warnings.extend(fmt.get("warnings", []))
            result.retry_feedback = f"Verification failed: {fmt['reason']}. Please correct."
            return result
        result.warnings.extend(fmt.get("warnings", []))


        struct = _check_structure(response)
        if not struct["valid"]:
            result.passed = False
            result.reason = struct["reason"]
            result.warnings.append(f"Structure: {struct['reason']}")
            result.retry_feedback = f"Structural: {struct['reason']}. Please ensure balanced brackets."
            return result

        return result


def verify(response: str, expected_format: Optional[str] = None) -> PostVerifyResult:
    return PostResponseVerifier().verify(response, expected_format=expected_format)



def _cli(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "verify":
        kwargs: Dict[str, str] = {}
        i = 0
        while i < len(rest):
            if rest[i].startswith("--") and i + 1 < len(rest):
                kwargs[rest[i][2:]] = rest[i + 1]
                i += 2
            else:
                i += 1
        response = kwargs.get("response", "")
        fmt = kwargs.get("format")
        r = PostResponseVerifier().verify(response, expected_format=fmt)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.passed else 1
    if cmd == "check-safety":
        text = " ".join(rest)
        s = _check_content_safety(text)
        print(json.dumps(s, indent=2))
        return 0 if not s["blocked"] else 1
    if cmd == "smoke":
        return _smoke()
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


def _smoke() -> int:

    r = PostResponseVerifier().verify("All good — 42.")
    assert r.passed
    print(f"  clean text: passed={r.passed}")


    r = PostResponseVerifier().verify("")
    assert not r.passed
    print(f"  empty: passed={r.passed}  reason={r.reason}")


    r = PostResponseVerifier().verify("api_key=\"abcdefghijklmnop123456\"")
    assert r.blocked
    print(f"  secret in text: blocked={r.blocked}  reason={r.reason[:60]}…")


    r = PostResponseVerifier().verify("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
    assert r.blocked
    print(f"  AWS key: blocked={r.blocked}")


    r = PostResponseVerifier().verify("My SSN is 123-45-6789")
    assert r.blocked
    print(f"  SSN: blocked={r.blocked}")


    r = PostResponseVerifier().verify('```json\n{"foo": 1}\n```', expected_format="json")
    assert r.passed
    print(f"  json format: passed={r.passed}")


    r = PostResponseVerifier().verify(
        '```json\n{"foo": 1}\n```', expected_format="json", required_fields=["foo", "bar"]
    )
    assert not r.passed
    print(f"  json missing fields: passed={r.passed}  reason={r.reason}")


    r = PostResponseVerifier().verify(
        '```json\n{"items": [1,2,3]}\n```', expected_format="json",
        schema={"items": "array"}
    )
    assert r.passed
    print(f"  json schema: passed={r.passed}")


    r = PostResponseVerifier().verify("```python\ndef foo(): pass\n```", expected_format="code")
    assert r.passed
    print(f"  code block: passed={r.passed}")


    r = PostResponseVerifier().verify("```python\ndef foo( ): pass\n```", expected_format="code")
    assert r.passed
    bad = "```python\ndef foo(): pass\n   return [1, 2\n```"
    r = PostResponseVerifier().verify(bad, expected_format="code")
    assert not r.passed
    print(f"  unbalanced brackets: passed={r.passed}")


    r = PostResponseVerifier().verify("Some text\n```", expected_format="markdown")
    assert "Unclosed code fence" in r.warnings
    print(f"  unclosed fence: warnings={r.warnings}")

    print("post_response_verifier: OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))