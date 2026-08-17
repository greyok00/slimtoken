#!/usr/bin/env python3
"""scripts/reframe.py — run slimtoken.prompt_reframe on stdin or argv.

Pipe a prompt in, get the tightened prompt + system + domain as JSON
out. Useful for batch jobs and ad-hoc experiments.

Usage:
  echo "can you basically just tell me what is the answer" \\
    | python3 scripts/reframe.py
  python3 scripts/reframe.py "your rambling prompt"
  python3 scripts/reframe.py --mode aggressive "long prompt here"

Exit codes:
  0  OK
  2  bad arguments
  3  rewriter raised (still writes JSON to stdout)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make slimtoken importable when run from the repo before install.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from slimtoken import prompt_reframe  # noqa: E402


def _render(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Tighten a user prompt with slimtoken.prompt_reframe.",
    )
    p.add_argument("prompt", nargs="?",
                   help="The user prompt (reads stdin if omitted).")
    p.add_argument("--mode", choices=["aggressive", "balanced", "preserve"],
                   default="balanced")
    p.add_argument("--max-tokens", type=int, default=0,
                   help="Override mode budget (word count).")
    p.add_argument("--role", default="generalist")
    p.add_argument("--style", default="terse")
    p.add_argument("--domain", default=None,
                   help="Override the auto-detected domain.")
    p.add_argument("--rules", action="append", default=[],
                   help="Add an explicit rule (repeatable).")
    args = p.parse_args(argv)

    if args.prompt is None:
        args.prompt = sys.stdin.read()
    text = (args.prompt or "").strip()
    if not text:
        print("error: empty prompt (pass it as argv or pipe on stdin)",
              file=sys.stderr)
        return 2

    try:
        domain = args.domain or prompt_reframe.classify_domain(text)
        reframed = prompt_reframe.reframe_prompt(text)
        kwargs = {"mode": args.mode}
        if args.max_tokens:
            kwargs["max_tokens"] = args.max_tokens
        shrunk = prompt_reframe.shrink_prompt(reframed, **kwargs)
        tight = prompt_reframe.minify_prompt(shrunk)
        system = prompt_reframe.build_system(
            domain, role=args.role, style=args.style,
            rules=tuple(args.rules),
        )
        out = {
            "domain": domain, "reframed": tight, "system": system,
            "stages": {
                "domain": domain,
                "reframe_len": len(reframed),
                "shrink_len": len(shrunk),
                "minify_len": len(tight),
                "input_len": len(text),
            },
        }
        sys.stdout.write(_render(out))
        sys.stdout.write("\n")
        return 0
    except Exception as e:
        sys.stdout.write(_render({"ok": False, "error": str(e)}))
        sys.stdout.write("\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
