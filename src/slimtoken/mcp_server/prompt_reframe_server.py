"""prompt_reframe_server — stdio MCP server exposing slimtoken's rewriter.

Speaks MCP JSON-RPC 2.0 over stdio (same wire format as the main
:mod:`slimtoken.mcp_server` server). The server is a thin adapter; it
calls the pure-stdlib functions in :mod:`slimtoken.prompt_reframe` and
wraps the results as MCP ``tools/call`` content blocks. No LLM roundtrip.

Why a separate server? The main ``slimtoken-mcp`` server exposes the
*request-body* minification pipeline (tools, system, messages, dedup,
distill, budget). This one exposes the *natural-language* rewriter
(classify / reframe / shrink / minify / build_system). Same protocol,
different toolkit — hosts that already wire a single MCP server can
choose either based on the kind of work their agent does.

Tool surface (``slimtoken.reframe.*`` namespace):

  slimtoken.reframe.classify_domain — keyword-match a prompt to a domain.
  slimtoken.reframe.reframe        — strip filler, dedupe, normalize.
  slimtoken.reframe.shrink         — TextRank-lite sentence ranking, cap.
  slimtoken.reframe.minify         — whitespace/punctuation squeeze.
  slimtoken.reframe.build_system   — compose a tight system prompt.
  slimtoken.reframe.frame          — run all five stages in one call.

All five primitives are dependency-free (Python 3.10+ stdlib), so this
server runs anywhere slimtoken is installed with no extras.

Usage:
  python -m slimtoken.mcp_server.prompt_reframe_server      # stdio
  slimtoken-reframe-mcp                                     # entry point
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from slimtoken import __version__ as SERVER_VERSION
from slimtoken import prompt_reframe

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "slimtoken-reframe-mcp"


# ── JSON-RPC plumbing ──────────────────────────────────────────────────────
def _send(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read() -> Optional[Dict[str, Any]]:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _result(req_id: Any, content: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id,
            "result": {"content": content, "isError": False}}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


# ── Tool schemas ────────────────────────────────────────────────────────────
def _schema_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "slimtoken.reframe.classify_domain",
            "description": ("Classify a natural-language prompt into one of "
                            "{business, professional, osint, cybersecurity, "
                            "code, general}. Keyword match, deterministic, "
                            "no LLM. Returns the domain label."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                               "description": "The user prompt to classify."},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "slimtoken.reframe.reframe",
            "description": ("Strip conversational filler (30+ phrases), drop "
                            "fragments, dedupe sentences, normalize "
                            "whitespace. Lossless on actionable claims."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                               "description": "The user prompt to reframe."},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "slimtoken.reframe.shrink",
            "description": ("Rank sentences by relevance + length (TextRank-"
                            "lite) and keep the top-N until the word budget "
                            "is met. Built from sentences that already exist "
                            "in the input — no semantic drift. Modes: "
                            "'aggressive' (~20 words), 'balanced' (~50), "
                            "'preserve' (~150). Default budget comes from "
                            "the mode; pass max_tokens to override."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                               "description": "The user prompt to shrink."},
                    "mode": {"type": "string", "enum": ["aggressive",
                                                          "balanced",
                                                          "preserve"],
                             "default": "balanced"},
                    "max_tokens": {"type": "integer", "minimum": 1,
                                   "description": "Optional word budget; "
                                                  "when omitted, the budget "
                                                  "is taken from `mode`."},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "slimtoken.reframe.minify",
            "description": ("Character-level squeeze: collapse whitespace, "
                            "drop redundant punctuation runs. Cosmetic."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                               "description": "The prompt to minify."},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "slimtoken.reframe.build_system",
            "description": ("Compose a tight, declarative system prompt from "
                            "a fixed schema (role / style / domain / rules). "
                            "Intentionally short."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string",
                               "description": "Domain label (any string; "
                                              "known labels get a hint)."},
                    "role": {"type": "string", "default": "generalist"},
                    "style": {"type": "string", "default": "terse"},
                    "rules": {"type": "array",
                              "items": {"type": "string"},
                              "description": "Up to 6 explicit rules."},
                },
                "required": ["domain"],
            },
        },
        {
            "name": "slimtoken.reframe.frame",
            "description": ("Run the full rewriter pipeline (classify → "
                            "reframe → shrink → minify → build_system) in "
                            "one call. Returns {domain, reframed, system}."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string",
                               "description": "The user prompt."},
                    "system_prompt": {"type": "string",
                                      "description": "Existing system; left "
                                                     "intact, composed system "
                                                     "appended below it."},
                    "mode": {"type": "string", "enum": ["aggressive",
                                                          "balanced",
                                                          "preserve"],
                             "default": "balanced"},
                    "role": {"type": "string", "default": "generalist"},
                    "style": {"type": "string", "default": "terse"},
                    "rules": {"type": "array",
                              "items": {"type": "string"}},
                },
                "required": ["prompt"],
            },
        },
    ]


def _content(obj: Any) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


# ── Tool handlers (thin; just call prompt_reframe) ─────────────────────────
def _call_classify_domain(args: Dict[str, Any]) -> Dict[str, Any]:
    prompt = args.get("prompt", "")
    return {"domain": prompt_reframe.classify_domain(prompt)}


def _call_reframe(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"reframed": prompt_reframe.reframe_prompt(args.get("prompt", ""))}


def _call_shrink(args: Dict[str, Any]) -> Dict[str, Any]:
    mt_raw = args.get("max_tokens", None)
    kwargs: Dict[str, Any] = {"mode": args.get("mode", "balanced")}
    if mt_raw is not None:
        kwargs["max_tokens"] = int(mt_raw)
    return {"shrunk": prompt_reframe.shrink_prompt(
        args.get("prompt", ""), **kwargs,
    )}


def _call_minify(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"minified": prompt_reframe.minify_prompt(args.get("prompt", ""))}


def _call_build_system(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"system": prompt_reframe.build_system(
        domain=args.get("domain", "general"),
        role=args.get("role", "generalist"),
        style=args.get("style", "terse"),
        rules=tuple(args.get("rules", []) or []),
    )}


def _call_frame(args: Dict[str, Any]) -> Dict[str, Any]:
    reframed, system, domain = prompt_reframe.frame_prompt(
        args.get("prompt", ""),
        system_prompt=args.get("system_prompt", "") or "",
        mode=args.get("mode", "balanced"),
        role=args.get("role", "generalist"),
        style=args.get("style", "terse"),
        rules=tuple(args.get("rules", []) or []),
    )
    return {"domain": domain, "reframed": reframed, "system": system}


_HANDLERS = {
    "slimtoken.reframe.classify_domain": _call_classify_domain,
    "slimtoken.reframe.reframe":        _call_reframe,
    "slimtoken.reframe.shrink":         _call_shrink,
    "slimtoken.reframe.minify":         _call_minify,
    "slimtoken.reframe.build_system":   _call_build_system,
    "slimtoken.reframe.frame":          _call_frame,
}


# ── Main loop ──────────────────────────────────────────────────────────────
def main() -> int:
    while True:
        msg = _read()
        if msg is None:
            return 0
        method = msg.get("method", "")
        req_id = msg.get("id")

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            }})
            continue

        if method == "tools/list":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {
                "tools": _schema_tools(),
            }})
            continue

        if method == "tools/call":
            params = msg.get("params", {}) or {}
            name = params.get("name", "")
            args = params.get("arguments", {}) or {}
            handler = _HANDLERS.get(name)
            if handler is None:
                _send(_error(req_id, -32601, f"unknown tool: {name}"))
                continue
            try:
                result = handler(args)
            except Exception as e:
                _send(_result(req_id, _content({
                    "ok": False, "error": f"{type(e).__name__}: {e}",
                })))
                continue
            _send(_result(req_id, _content({"ok": True, "data": result})))
            continue

        if method == "notifications/initialized":
            continue

        if req_id is not None:
            _send(_error(req_id, -32601,
                         f"{SERVER_NAME}: unknown method {method!r}"))


if __name__ == "__main__":
    raise SystemExit(main())
