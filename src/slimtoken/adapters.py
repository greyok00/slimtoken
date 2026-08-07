"""adapters — bidirectional conversion between OpenAI/Ollama chat format and the
Anthropic canonical form slimtoken's pipeline operates on.

The minify pipeline (:func:`slimtoken.pipeline.minify_request`) is built around
Anthropic's request shape: a top-level ``system`` string, ``messages`` whose
``content`` may be a list of typed blocks (``text`` / ``tool_use`` /
``tool_result``), and ``tools`` with ``input_schema``. OpenAI and Ollama use a
different shape: system is a ``role:"system"`` message, tool calls live in
``assistant.tool_calls``, tool results are separate ``role:"tool"`` messages,
and tools carry ``function.parameters``.

This module is a **thin shim around the frozen pipeline**. It converts an
OpenAI/Ollama body to canonical Anthropic form, the pipeline minifies it, then
it converts back. No optimization logic is reimplemented; the Anthropic path is
identity (zero work). ``ollama`` reuses the OpenAI conversion — their
``messages`` / ``tools`` structures match; only the endpoint differs.

Pair-safety is preserved across the round trip: an OpenAI ``assistant.tool_calls``
plus its following ``role:"tool"`` replies become an Anthropic
``tool_use`` block plus ``tool_result`` blocks; the pipeline drops such pairs
together, so the reverse conversion never orphans a tool result from its call.

Limitations: image / audio content blocks pass through best-effort (the proxy
does not minify them); the common text + tools case — what the pipeline targets
— converts losslessly.
"""
from __future__ import annotations

import json
from typing import Any, Optional

CANONICAL = "anthropic"
OPENAI = "openai"
OLLAMA = "ollama"

# fields we rebuild during conversion (never carried through as-is)
_REBUILD = {"messages", "tools", "system"}


def detect(path: str) -> Optional[str]:
    """Infer the request format from the URL path. None if unknown."""
    p = path.split("?", 1)[0].rstrip("/")
    if p.endswith("/v1/messages"):
        return CANONICAL
    if p.endswith("/v1/chat/completions"):
        return OPENAI
    if p.endswith("/api/chat") or p.endswith("/api/generate"):
        return OLLAMA
    return None


# ── helpers ────────────────────────────────────────────────────────────────────
def _flatten_text(content: Any) -> str:
    """Coerce an OpenAI content field (string or list of {type:text,text}) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("text", None) and "text" in b:
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return str(content)


def _parse_args(args: Any) -> Any:
    """OpenAI tool_calls.function.arguments is a JSON string; Anthropic input is a dict."""
    if isinstance(args, str):
        try:
            return json.loads(args)
        except Exception:
            return {"_raw": args}
    return args if isinstance(args, dict) else {}


# ── OpenAI/Ollama → Anthropic canonical ─────────────────────────────────────────
def _openai_tool_to_anthropic(t: dict) -> dict:
    fn = t.get("function", {}) if isinstance(t, dict) else {}
    return {
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters", {}) or {"type": "object", "properties": {}},
    }


def to_canonical(body: dict, fmt: str) -> dict:
    """Normalize an OpenAI/Ollama request body to Anthropic canonical form.

    ``anthropic`` is identity. Carries through every non-message/tool/system field
    (``model``, ``max_tokens``, ``temperature``, ``stream``, Ollama ``options`` /
    ``format`` / ``keep_alive``, …) untouched so they survive the round trip.
    """
    if fmt == CANONICAL or not isinstance(body, dict):
        return body
    out = {k: v for k, v in body.items() if k not in _REBUILD}

    msgs_in = body.get("messages") or []
    sys_parts: list = []
    canon_msgs: list = []
    pending_tool_results: list = []

    def _flush_results():
        nonlocal pending_tool_results
        if pending_tool_results:
            canon_msgs.append({"role": "user", "content": pending_tool_results})
            pending_tool_results = []

    for m in msgs_in:
        if not isinstance(m, dict):
            canon_msgs.append(m)
            continue
        role = m.get("role")
        content = m.get("content")

        if role == "system":
            sys_parts.append(_flatten_text(content))
            continue

        if role == "tool":
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "content": _flatten_text(content),
            })
            continue

        _flush_results()

        if role == "assistant":
            blocks: list = []
            txt = _flatten_text(content)
            if txt:
                blocks.append({"type": "text", "text": txt})
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {}) if isinstance(tc.get("function"), dict) else {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": _parse_args(fn.get("arguments", "{}")),
                })
            if blocks:
                canon_msgs.append({"role": "assistant", "content": blocks})
            continue

        if role == "user":
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and "text" in b]
                canon_msgs.append({"role": "user", "content": "".join(texts)})
            else:
                canon_msgs.append({"role": "user", "content": _flatten_text(content)})
            continue

        # unknown role: pass through verbatim (best effort)
        canon_msgs.append(m)

    _flush_results()

    if sys_parts:
        out["system"] = "\n\n".join(s for s in sys_parts if s)
    out["messages"] = canon_msgs

    tools_in = body.get("tools")
    if tools_in:
        out["tools"] = [_openai_tool_to_anthropic(t) for t in tools_in]
    return out


# ── Anthropic canonical → OpenAI/Ollama ────────────────────────────────────────
def _anthropic_tool_to_openai(t: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {}) or {"type": "object", "properties": {}},
        },
    }


def from_canonical(body: dict, fmt: str) -> dict:
    """Denormalize an Anthropic canonical body back to OpenAI/Ollama form.

    ``anthropic`` is identity. Rebuilds system messages, assistant ``tool_calls``,
    and ``role:"tool"`` result messages; carries every other field through.
    """
    if fmt == CANONICAL or not isinstance(body, dict):
        return body
    out = {k: v for k, v in body.items() if k not in _REBUILD}

    msgs_out: list = []
    sys = body.get("system")
    if sys is not None:
        if isinstance(sys, list):
            sys_text = "\n\n".join(b.get("text", "") for b in sys
                                   if isinstance(b, dict) and b.get("type") == "text")
        else:
            sys_text = sys if isinstance(sys, str) else _flatten_text(sys)
        if sys_text:
            msgs_out.append({"role": "system", "content": sys_text})

    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            msgs_out.append(m)
            continue
        role = m.get("role")
        content = m.get("content")

        if role == "user":
            if isinstance(content, list):
                text_parts, tool_results = [], []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_result":
                        tc = b.get("content", "")
                        if isinstance(tc, list):
                            tc = "".join(x.get("text", "") for x in tc
                                         if isinstance(x, dict) and x.get("type") == "text")
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id", ""),
                            "content": tc if isinstance(tc, str) else str(tc),
                        })
                    elif b.get("type") == "text" or "text" in b:
                        text_parts.append(b.get("text", ""))
                msgs_out.extend(tool_results)
                if text_parts:
                    msgs_out.append({"role": "user", "content": "".join(text_parts)})
            else:
                msgs_out.append({"role": "user",
                                 "content": content if content is not None else ""})
            continue

        if role == "assistant":
            if isinstance(content, list):
                text_parts, tool_calls = [], []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        inp = b.get("input", {})
                        args = json.dumps(inp) if not isinstance(inp, str) else inp
                        tool_calls.append({
                            "id": b.get("id", ""),
                            "type": "function",
                            "function": {"name": b.get("name", ""), "arguments": args},
                        })
                    elif b.get("type") == "text" or "text" in b:
                        text_parts.append(b.get("text", ""))
                msg: dict = {"role": "assistant"}
                msg["content"] = "".join(text_parts) if text_parts else None
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                msgs_out.append(msg)
            else:
                msgs_out.append({"role": "assistant",
                                 "content": content if content is not None else ""})
            continue

        msgs_out.append(m)

    out["messages"] = msgs_out

    tools = body.get("tools")
    if tools:
        out["tools"] = [_anthropic_tool_to_openai(t) for t in tools]
    return out


def roundtrip(body: dict, fmt: str) -> dict:
    """to_canonical → from_canonical. For testing / inspection."""
    return from_canonical(to_canonical(body, fmt), fmt) if fmt != CANONICAL else body