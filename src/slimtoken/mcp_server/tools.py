"""tools — MCP tool schemas + thin handlers that call EXISTING pipeline functions.

The MCP server is a thin adapter. Every handler imports and calls a function
that already exists in slimtoken's core; none of them reimplement any
optimization logic. Tool names are dotted (``slimtoken.optimize_messages``) to
namespace them clearly in a client's tool list.

Handlers return plain Python objects; :func:`to_content` wraps them as an MCP
``tools/call`` result (a list of content blocks). On error, handlers raise or
return an ``isError`` content block — the server converts either form.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from typing import Any, Dict, List

from ..pipeline import minify_request
from ..tokencount import count_messages, count_obj, count_system, count_tools
from ..context_prune import prune_context
from ..tool_result_compress import compress_content
from ..token_budget import enforce_budget
from ..profiles import profile_config, profile_doc, profile_names
from ..model_presets import list_presets, preset_with_reduction
from ..adapters import to_canonical, from_canonical, CANONICAL
from ..context_presets import list_context_presets, best_context_for_tier


# ── JSON schemas ────────────────────────────────────────────────────────────
def _schema_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": "slimtoken.optimize_messages",
            "description": ("Reduce prompt size while preserving message structure and "
                            "tool-call validity (pair-safe, fence-aware). Returns the minified "
                            "messages plus token counts. Lossless for the 'safe' profile; "
                            "'aggressive' enables lossy tool-result compression."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "messages": {"type": "array", "description": "Anthropic-style messages array"},
                    "system": {"description": "system prompt (string or list of text blocks)",
                               "type": ["string", "array"]},
                    "tools": {"type": "array", "description": "tool definitions to minify"},
                    "profile": {"type": "string", "enum": profile_names(),
                                "default": "aggressive",
                                "description": "aggressiveness preset (existing MinifyConfig knobs)"},
                    "max_input_tokens": {"type": "integer",
                                         "description": "override the profile's token_budget (hard prune cap)"},
                    "format": {"type": "string", "enum": ["anthropic", "openai", "ollama"],
                               "default": "anthropic",
                               "description": "request format (anthropic=identity; openai/ollama "
                                              "are normalized to canonical, minified, then returned)"},
                },
                "required": ["messages"],
            },
        },
        {
            "name": "slimtoken.estimate_tokens",
            "description": ("Count tokens in a request body using the real cl100k_base tokenizer "
                            "(bundled, offline). Returns total + per-message breakdown. The "
                            "`model` arg is accepted for forward-compat but the count is "
                            "cl100k-approximate for non-cl100k models."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "messages": {"type": "array"},
                    "system": {"type": ["string", "array"]},
                    "tools": {"type": "array"},
                    "model": {"type": "string", "description": "model name (informational only)"},
                    "format": {"type": "string", "enum": ["anthropic", "openai", "ollama"],
                               "default": "anthropic",
                               "description": "request format of the body (normalized to canonical before counting)"},
                },
                "required": ["messages"],
            },
        },
        {
            "name": "slimtoken.prune_context",
            "description": ("RAG-style context pruning for a memory/conversation store: strip "
                            "low-value text, retrieve warm entries relevant to a query, "
                            "sliding-window summarize old turns, and enforce a token budget. "
                            "Returns a ready-to-inject <cold_memory>/<recent_context> prompt block."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cold_data": {"type": "object",
                                  "description": "cold memory keyed by category (each value is a list of entries)"},
                    "warm_entries": {"type": "array",
                                     "description": "warm/conversation entries (role+content dicts)"},
                    "query": {"type": "string", "description": "current query for relevance retrieval"},
                    "max_tokens": {"type": "integer", "default": 2000},
                },
                "required": ["warm_entries"],
            },
        },
        {
            "name": "slimtoken.minify_tool_result",
            "description": ("Compress a large tool_result content block using type detection "
                            "(directory listing, git output, logs, JSON, source). LOSSY — emits a "
                            "compact representation plus a [slimtoken-compressed] metadata header. "
                            "Pair-safe by construction (only the content field changes)."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"description": "tool_result content (string or list of text blocks)",
                                "type": ["string", "array"]},
                },
                "required": ["content"],
            },
        },
        {
            "name": "slimtoken.inspect_budget",
            "description": ("Read-only token-budget inspection: counts system/tools/messages, "
                            "reports headroom against a token_budget, and whether the pair-safe "
                            "pruner would drop any leading messages. Does not modify the body."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "messages": {"type": "array"},
                    "system": {"type": ["string", "array"]},
                    "tools": {"type": "array"},
                    "token_budget": {"type": "integer", "default": 131072},
                    "keep_last": {"type": "integer", "default": 8},
                    "format": {"type": "string", "enum": ["anthropic", "openai", "ollama"],
                               "default": "anthropic",
                               "description": "request format of the body (normalized to canonical before inspection)"},
                },
                "required": ["messages"],
            },
        },
        {
            "name": "slimtoken.get_config",
            "description": ("Return the slimtoken config in use. With a `profile`, returns that "
                            "profile's MinifyConfig; without, returns the proxy's env-derived "
                            "config (SLIMTOKEN_* env). Useful to see what the proxy will do."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string", "enum": profile_names()},
                },
            },
        },
        {
            "name": "slimtoken.list_model_presets",
            "description": ("List recommended local-model presets by GPU VRAM tier (4/8/16/24GB), "
                            "each with a slimtoken profile and usable context. With measure=true, "
                            "enriches each row with the live measured token reduction on a bloated "
                            "payload (run by the pipeline itself)."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "vram_gb": {"type": "integer", "description": "filter to one tier (4/8/16/24)"},
                    "measure": {"type": "boolean", "default": False,
                                "description": "run the pipeline to measure real reduction"},
                },
            },
        },
        {
            "name": "slimtoken.high_context_presets",
            "description": ("High-context VRAM-tier configs (dense AND MoE) showing how slimtoken "
                            "compression expands the effective context window. Each row gives the "
                            "largest nominal context that fits fully in VRAM (computed by "
                            "config_optimizer, q4_0 KV, flash attn, full offload) and the effective "
                            "raw-token capacity = nominal_ctx / (1 - reduction). Use best=true for "
                            "just the largest-effective-context preset of a tier."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "vram_gb": {"type": "integer", "description": "filter to one tier (4/8/16/24)"},
                    "best": {"type": "boolean", "default": False,
                             "description": "return only the largest-effective-context preset for the tier"},
                },
            },
        },
    ]


def tools_list() -> List[Dict[str, Any]]:
    return _schema_tools()


# ── handlers ────────────────────────────────────────────────────────────────
def _as_body(arguments: Dict[str, Any]) -> dict:
    body: dict = {}
    if "messages" in arguments:
        body["messages"] = arguments["messages"]
    if arguments.get("system") is not None:
        body["system"] = arguments["system"]
    if arguments.get("tools") is not None:
        body["tools"] = arguments["tools"]
    return body


def _canonical_body(arguments: Dict[str, Any]) -> tuple:
    """Build the body from args and normalize to Anthropic canonical.

    Returns (body, fmt). When fmt != anthropic, OpenAI/Ollama messages/tools/system
    are converted to canonical form so the frozen pipeline can minify them; callers
    that return a body convert back with from_canonical(body, fmt).
    """
    fmt = arguments.get("format", "anthropic") or "anthropic"
    body = _as_body(arguments)
    if fmt != CANONICAL:
        body = to_canonical(body, fmt)
    return body, fmt


def _cfg_dict(cfg) -> dict:
    d = asdict(cfg)
    d["enabled_stages"] = sorted(d.get("enabled_stages") or set())
    d["tool_skip"] = sorted(d.get("tool_skip") or set())
    return d


class ToolError(Exception):
    pass


def handle(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a tool call. Returns a result object (NOT yet MCP-wrapped)."""
    if name == "slimtoken.optimize_messages":
        return _t_optimize_messages(arguments)
    if name == "slimtoken.estimate_tokens":
        return _t_estimate_tokens(arguments)
    if name == "slimtoken.prune_context":
        return _t_prune_context(arguments)
    if name == "slimtoken.minify_tool_result":
        return _t_minify_tool_result(arguments)
    if name == "slimtoken.inspect_budget":
        return _t_inspect_budget(arguments)
    if name == "slimtoken.get_config":
        return _t_get_config(arguments)
    if name == "slimtoken.list_model_presets":
        return _t_list_model_presets(arguments)
    if name == "slimtoken.high_context_presets":
        return _t_high_context_presets(arguments)
    raise ToolError(f"unknown tool: {name}")


def _t_optimize_messages(a: Dict[str, Any]) -> Dict[str, Any]:
    msgs = a.get("messages")
    if not isinstance(msgs, list):
        raise ToolError("messages must be an array")
    profile = a.get("profile", "aggressive")
    fmt = a.get("format", "anthropic") or "anthropic"
    cfg = profile_config(profile)
    if a.get("max_input_tokens") is not None:
        cfg.token_budget = int(a["max_input_tokens"])
    body, _ = _canonical_body(a)
    tin = count_obj(body)
    out, stats = minify_request(copy.deepcopy(body), cfg)
    tout = count_obj(out)
    # return the optimized body in the caller's format (anthropic=identity)
    if fmt != CANONICAL:
        out = from_canonical(out, fmt)
    return {
        "messages": out.get("messages", msgs),
        "system": out.get("system") if "system" in out else None,
        "tools": out.get("tools") if "tools" in out else None,
        "tokens_in": tin, "tokens_out": tout,
        "reduction_pct": round(100 * (tin - tout) / tin, 1) if tin else 0.0,
        "profile": profile,
        "format": fmt,
        "stats": {
            "tools_minified": stats.tools_minified,
            "system_minified": stats.system_minified,
            "messages_minified": stats.messages_minified,
            "dedup_count": stats.dedup_count,
            "distill_count": stats.distill_count,
            "budget_dropped": stats.budget_dropped,
            "tool_compressed": stats.tool_compressed,
            "errors": list(stats.errors),
        },
    }


def _t_estimate_tokens(a: Dict[str, Any]) -> Dict[str, Any]:
    msgs = a.get("messages")
    if not isinstance(msgs, list):
        raise ToolError("messages must be an array")
    body, fmt = _canonical_body(a)
    total = count_obj(body)
    sys_tok = count_system(body.get("system")) if "system" in body else 0
    tools_tok = count_tools(body.get("tools")) if "tools" in body else 0
    # count against the (possibly converted) canonical messages so the per-msg
    # breakdown matches what the pipeline will see
    canon_msgs = body.get("messages", msgs)
    msg_total, per_msg = count_messages(canon_msgs)
    return {
        "tokens": total,
        "system_tokens": sys_tok,
        "tools_tokens": tools_tok,
        "messages_tokens": msg_total,
        "per_message": per_msg,
        "tokenizer": "cl100k_base (bundled, offline)",
        "note": ("count is exact for cl100k models (e.g. Claude/GPT); approximate for "
                 "others (Llama, Qwen, Gemma) — used for budgeting, not billing"),
        "model": a.get("model"),
        "format": fmt,
    }


def _t_prune_context(a: Dict[str, Any]) -> Dict[str, Any]:
    warm = a.get("warm_entries")
    if not isinstance(warm, list):
        raise ToolError("warm_entries must be an array")
    cold = a.get("cold_data") or None
    if cold is not None and not isinstance(cold, dict):
        raise ToolError("cold_data must be an object")
    r = prune_context(cold, warm, query=a.get("query", ""),
                      max_tokens=int(a.get("max_tokens", 2000)))
    return {"prompt_block": r.to_prompt_block(),
            "token_count": r.token_count, "stats": r.stats}


def _t_minify_tool_result(a: Dict[str, Any]) -> Dict[str, Any]:
    content = a.get("content")
    if content is None:
        raise ToolError("content is required")
    nc, changed = compress_content(content)
    return {"changed": changed, "content": nc if changed else content}


def _t_inspect_budget(a: Dict[str, Any]) -> Dict[str, Any]:
    msgs = a.get("messages")
    if not isinstance(msgs, list):
        raise ToolError("messages must be an array")
    body, fmt = _canonical_body(a)
    canon_msgs = body.get("messages", msgs)
    budget = int(a.get("token_budget", 131072))
    keep_last = int(a.get("keep_last", 8))
    sys_tok = count_system(body.get("system")) if "system" in body else 0
    tools_tok = count_tools(body.get("tools")) if "tools" in body else 0
    msg_total, per_msg = count_messages(canon_msgs)
    total = sys_tok + tools_tok + msg_total
    stats: Dict[str, Any] = {}
    would_drop = 0
    after = total
    if budget > 0 and "messages" in body:
        probe = copy.deepcopy(body)
        enforce_budget(probe, budget, keep_last=keep_last, stats=stats)
        would_drop = int(stats.get("budget_dropped", 0) or 0)
        after = int(stats.get("budget_tokens_after", total) or total)
    return {"total_tokens": total, "system_tokens": sys_tok,
            "tools_tokens": tools_tok, "messages_tokens": msg_total,
            "token_budget": budget, "headroom": budget - total,
            "over_budget": total > budget, "would_drop_messages": would_drop,
            "tokens_after_drop": after, "keep_last": keep_last,
            "per_message": per_msg, "format": fmt}


def _t_get_config(a: Dict[str, Any]) -> Dict[str, Any]:
    profile = a.get("profile")
    if profile:
        cfg = profile_config(profile)
        return {"profile": profile, "config": _cfg_dict(cfg),
                "description": profile_doc().get(profile)}
    # proxy env-derived config (lazy import so the MCP process need not import
    # httpx/asyncio unless this tool is actually called)
    from ..proxy import build_minify_cfg
    cfg = build_minify_cfg()
    return {"config": _cfg_dict(cfg), "source": "SLIMTOKEN_* env (proxy config)"}


def _t_list_model_presets(a: Dict[str, Any]) -> Dict[str, Any]:
    vram = a.get("vram_gb")
    if a.get("measure"):
        rows = preset_with_reduction(vram)
    else:
        rows = list_presets(vram)
    return {"presets": rows, "count": len(rows)}


def _t_high_context_presets(a: Dict[str, Any]) -> Dict[str, Any]:
    vram = a.get("vram_gb")
    if a.get("best"):
        row = best_context_for_tier(vram) if vram is not None else None
        return {"best": row, "count": 1 if row else 0}
    rows = list_context_presets(vram)
    return {"presets": rows, "count": len(rows)}


# ── MCP content wrapping ────────────────────────────────────────────────────
def to_content(result: Any) -> Dict[str, Any]:
    """Wrap a handler result as an MCP tools/call success content block."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def to_error(msg: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"slimtoken error: {msg}"}], "isError": True}