"""proxy — the drop-in async HTTP(S) optimization proxy.

Point ANTHROPIC_BASE_URL at this proxy (default 127.0.0.1:8181). Per request it:
  1. reads the request (async; body buffered once — only the response streams)
  2. fast-paths small / unoptimized requests as raw bytes (no JSON parse)
  3. otherwise parses, strips `grammar`, runs the minify pipeline, re-serializes
  4. forwards to the upstream via a shared, keep-alive httpx.AsyncClient
  5. streams the response back as RAW bytes (no per-chunk parse), tracking only
     the final usage event for /metrics
  6. records t0..t4 latency timestamps separating proxy work from model generation

Pipeline (minify_request) stays pure sync CPU — called inline (~3 ms; fine on
the event loop at LLM-proxy concurrency). The transport is fully async with
connection reuse, so concurrent requests don't serialize on upstream connects.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import httpx

from .pipeline import minify_request, MinifyConfig
from .upstream import Upstream
from ._deps import jloads, jdumps
from . import adapters
from . import __version__

# ── latency / token metrics ──────────────────────────────────────────────────
# Single-threaded asyncio — no lock needed for dict updates.
_metrics: Dict = {
    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    "requests": 0, "total_time_s": 0.0, "started_at": datetime.now().isoformat(),
    "current_tok_s": 0.0, "avg_tok_s": 0.0,
    # latency buckets (seconds), accumulated per request
    "latency": {
        "proxy_ingress": 0.0,    # t1-t0  read request
        "optimize": 0.0,         # t2-t1  parse + minify
        "ttft": 0.0,             # t3-t2  forward -> first output token
        "generation": 0.0,       # t4-t3  first -> final token
        "total": 0.0,            # t4-t0
        "samples": 0,
    },
}
_HOP_BY_HOP = {"host", "content-length", "transfer-encoding", "expect",
               "connection", "keep-alive", "proxy-connection", "te", "trailer",
               "upgrade"}


def _record(pt: int, ct: int, elapsed: float, lat: dict):
    _metrics["prompt_tokens"] += pt
    _metrics["completion_tokens"] += ct
    _metrics["total_tokens"] += pt + ct
    _metrics["requests"] += 1
    _metrics["total_time_s"] += elapsed
    if elapsed > 0 and ct > 0:
        _metrics["current_tok_s"] = round(ct / elapsed, 1)
    if _metrics["total_time_s"] > 0 and _metrics["completion_tokens"] > 0:
        _metrics["avg_tok_s"] = round(
            _metrics["completion_tokens"] / _metrics["total_time_s"], 1)
    L = _metrics["latency"]
    L["proxy_ingress"] = round(L["proxy_ingress"] + lat.get("proxy_ingress", 0), 4)
    L["optimize"] = round(L["optimize"] + lat.get("optimize", 0), 4)
    L["ttft"] = round(L["ttft"] + lat.get("ttft", 0), 4)
    L["generation"] = round(L["generation"] + lat.get("generation", 0), 4)
    L["total"] = round(L["total"] + lat.get("total", 0), 4)
    L["samples"] += 1


def _metrics_json() -> str:
    m = dict(_metrics)
    m = {**m, "latency": dict(m["latency"])}
    return json.dumps(m, indent=2)


# ── minify config from env ────────────────────────────────────────────────────
def _bool_env(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except ValueError:
        return default


def build_minify_cfg() -> MinifyConfig:
    if not _bool_env("SLIMTOKEN_MINIFY", True):
        return MinifyConfig(enabled_stages=set())
    stages = set()
    if _bool_env("SLIMTOKEN_MINIFY_TOOLS", True):
        stages.add("tools")
    if _bool_env("SLIMTOKEN_MINIFY_SYSTEM", True):
        stages.add("system")
    if _bool_env("SLIMTOKEN_MINIFY_MESSAGES", True):
        stages.add("messages")
    if _bool_env("SLIMTOKEN_MINIFY_DEDUP", True):
        stages.add("dedup")
    if _bool_env("SLIMTOKEN_MINIFY_DISTILL", True):
        stages.add("distill")
    skip = {s.strip() for s in os.environ.get(
        "SLIMTOKEN_MINIFY_TOOL_SKIP", "").split(",") if s.strip()}
    budget = _int_env("SLIMTOKEN_MINIFY_BUDGET", 131072)
    return MinifyConfig(
        token_budget=budget,
        enabled_stages=stages,
        tool_skip=skip,
        keep_last=_int_env("SLIMTOKEN_KEEP_LAST", 8),
        dedup_min_chars=_int_env("SLIMTOKEN_DEDUP_MIN_CHARS", 200),
        distill_max_chars=_int_env("SLIMTOKEN_DISTILL_MAX_CHARS", 240),
        tool_compress=_bool_env("SLIMTOKEN_TOOL_COMPRESS", False),
    )


_CFG = build_minify_cfg()


# Output filter (Phase C): active only when SLIMTOKEN_MAX_TOKENS or
# SLIMTOKEN_STOP is set; otherwise None → raw passthrough, zero overhead.
def _build_out_filter():
    try:
        from .output_filter import from_env
        return from_env()
    except Exception as e:
        print(f"[proxy] output_filter unavailable: {e}", file=sys.stderr)
        return None


# Built once at import (env is process-wide for the proxy process).
_OUT_FILTER = _build_out_filter()


# ── request context + timestamps ──────────────────────────────────────────────
@dataclass
class RequestContext:
    request_id: int = 0
    t0: float = 0.0   # ingress
    t1: float = 0.0   # request fully read
    t2: float = 0.0   # optimized (parse + minify done)
    t3: float = 0.0   # first output token received from upstream
    t4: float = 0.0   # final token / stream end
    pt: int = 0
    ct: int = 0

    def lat(self) -> dict:
        return {
            "proxy_ingress": max(0.0, self.t1 - self.t0),
            "optimize": max(0.0, self.t2 - self.t1),
            "ttft": max(0.0, self.t3 - self.t2),
            "generation": max(0.0, self.t4 - self.t3),
            "total": max(0.0, self.t4 - self.t0),
        }


# ── chunked body decode (stdlib, pure) ─────────────────────────────────────────
def _dechunk(data: bytes):
    out = bytearray(); i = 0; n = len(data)
    while True:
        crlf = data.find(b"\r\n", i)
        if crlf < 0:
            return None
        try:
            size = int(data[i:crlf].split(b";")[0].strip(), 16)
        except ValueError:
            return None
        i = crlf + 2
        if size == 0:
            break
        if i + size + 2 > n:
            return None
        out += data[i:i + size]
        i += size + 2
    return bytes(out)


# ── fast-path decision ────────────────────────────────────────────────────────
_FAST_PATH_MAX = 4096  # bodies under this AND no minify stages → raw passthrough


def _is_fast_path(body: bytes) -> bool:
    """Raw passthrough when minify is OFF, or the body is small AND no stages
    target it (no tool_results to dedup, short history). Conservative: only skip
    when there is genuinely nothing to optimize."""
    if not _CFG.enabled_stages:
        return True
    if len(body) > _FAST_PATH_MAX:
        return False
    # cheap byte probe (no full parse): look for signals that stages would act on.
    # covers Anthropic (tool_result/system) AND OpenAI/Ollama (tool_calls/role:tool).
    if (b'"tool_result"' in body or b'"tools"' in body or b'"system"' in body
            or b'"tool_calls"' in body or b'"role": "tool"' in body
            or b'"role":"tool"' in body):
        return False
    # small body with no tool_result/tools/system — only messages, and minify of
    # short text is ~zero gain. Still, messages minify collapses blanks; to be
    # safe and preserve behavior, only fast-path when minify master is off.
    return False


# ── minify (sync CPU; inline on the loop) ──────────────────────────────────────
def _minify_body(body: bytes, fmt: str = "anthropic") -> bytes:
    try:
        parsed = jloads(body)
    except Exception as e:
        print(f"[proxy] body parse failed (passthrough): {e}", file=sys.stderr)
        return body
    if isinstance(parsed, dict):
        if "grammar" in parsed:
            del parsed["grammar"]
        # normalize to Anthropic canonical for the (frozen) pipeline, then back.
        # fmt="anthropic" (default) skips both branches → byte-identical to before.
        if fmt != "anthropic":
            parsed = adapters.to_canonical(parsed, fmt)
        if _CFG.enabled_stages:
            parsed, stats = minify_request(parsed, _CFG)
            print(f"[proxy] minify ({fmt}): {stats.summary()}", file=sys.stderr)
        if fmt != "anthropic":
            parsed = adapters.from_canonical(parsed, fmt)
        return jdumps(parsed)
    return body


# ── usage extraction from a rolling tail of the streamed response ────────────
def _extract_usage(tail: bytes) -> tuple:
    """Best-effort parse of usage from the response tail. Handles BOTH:
      - SSE streams: the final `data: {...usage...}` event
      - plain JSON:  a single body with a top-level `usage` field
    Returns (prompt_tokens, completion_tokens). 0,0 if not found."""
    pt, ct = 0, 0
    # 1. SSE: find the last `data: ` line containing a usage object
    has_data = b"\ndata:" in tail or tail.lstrip().startswith(b"data:")
    if has_data:
        for line in tail.split(b"\n"):
            s = line.strip()
            if not s.startswith(b"data:"):
                continue
            payload = s[5:].lstrip()
            if b"usage" not in payload and b"_tokens" not in payload:
                continue
            try:
                obj = jloads(payload)
            except Exception:
                continue
            u = obj.get("usage", {}) if isinstance(obj, dict) else {}
            if not isinstance(u, dict) or not u:
                continue
            pt = u.get("prompt_tokens") or u.get("input_tokens") or pt
            ct = u.get("completion_tokens") or u.get("output_tokens") or ct
            if pt or ct:
                return pt, ct
    # 2. plain JSON body with a top-level usage field
    if not has_data:
        try:
            obj = jloads(tail)
        except Exception:
            return pt, ct
        if isinstance(obj, dict):
            u = obj.get("usage")
            if isinstance(u, dict) and u:
                pt = u.get("prompt_tokens") or u.get("input_tokens") or 0
                ct = u.get("completion_tokens") or u.get("output_tokens") or 0
    return pt, ct


# ── async request handler ─────────────────────────────────────────────────────
async def _read_request(reader: asyncio.StreamReader) -> Optional[tuple]:
    """Read (method, path, headers, body). None on malformed/empty."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        return None
    text = head.decode("utf-8", errors="replace")
    lines = text.split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2:
        return None
    method, path = parts[0].upper(), parts[1]
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    body = b""
    te = headers.get("transfer-encoding", "").lower()
    cl = headers.get("content-length")
    if "chunked" in te:
        # read until the terminating 0-chunk
        term = b"\r\n0\r\n\r\n"
        try:
            body = await reader.readuntil(term)
        except Exception:
            pass
        body = _dechunk(body or b"") or body
    elif cl is not None:
        try:
            body = await reader.readexactly(int(cl))
        except Exception:
            pass
    return method, path, headers, body


def _forward_headers(headers: dict, host: str, length: int) -> dict:
    out = {}
    for k, v in headers.items():
        if k in _HOP_BY_HOP:
            continue
        if k == "user-agent":
            out["User-Agent"] = f"slimtoken/{__version__}"
        else:
            # preserve original case-ish
            out[k] = v
    out["Host"] = host
    out["Content-Length"] = str(length)
    out["Connection"] = "close"
    return out


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                   client: httpx.AsyncClient, upstream: Upstream, req_id: int):
    ctx = RequestContext(request_id=req_id, t0=time.perf_counter())
    try:
        req = await _read_request(reader)
        if req is None:
            return
        method, path, headers, body = req
        ctx.t1 = time.perf_counter()

        if method == "GET" and path == "/metrics":
            m = _metrics_json().encode()
            head = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(m)}\r\nConnection: close\r\n\r\n").encode() + m
            writer.write(head)
            await writer.drain()
            return

        # optimize (or fast-path passthrough, unparsed). route by path to detect
        # the request format (anthropic /v1/messages, openai /v1/chat/completions,
        # ollama /api/chat); anthropic (default) skips the adapter branches.
        if method == "POST" and not _is_fast_path(body):
            out_body = _minify_body(body, adapters.detect(path) or "anthropic")
        else:
            out_body = body
        ctx.t2 = time.perf_counter()

        url = upstream.base_url + path
        fwd = _forward_headers(headers, f"{upstream.host}:{upstream.port}", len(out_body))

        # forward + stream response back to the client. aiter_bytes() yields
        # DECOMPRESSED bytes (httpx auto-decodes content-encoding), so we strip
        # content-encoding/length/TE upstream and re-emit with Connection: close
        # (HTTP/1.1 allows a body delimited by connection close).
        tail = bytearray()  # rolling tail for usage extraction (capped)
        first = True
        try:
            async with client.stream(method, url, content=out_body, headers=fwd,
                                      timeout=httpx.Timeout(600.0, connect=10.0)) as resp:
                out_h = [f"HTTP/1.1 {resp.status_code} {resp.reason_phrase}\r\n"]
                for k, v in resp.headers.items():
                    if k.lower() in ("transfer-encoding", "connection",
                                     "content-length", "content-encoding"):
                        continue
                    out_h.append(f"{k}: {v}\r\n")
                out_h.append("Connection: close\r\n")
                writer.write(("".join(out_h) + "\r\n").encode())
                await writer.drain()

                # output filter (max_tokens / stop enforcement). Inert (None)
                # when neither SLIMTOKEN_MAX_TOKENS nor SLIMTOKEN_STOP is set —
                # then we stream raw bytes with zero per-chunk overhead.
                out_filter = _OUT_FILTER
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if first:
                        ctx.t3 = time.perf_counter()
                        first = False
                    emit = out_filter.feed(chunk) if out_filter is not None else chunk
                    if emit:
                        writer.write(emit)
                        await writer.drain()
                    if out_filter is not None and out_filter._closed:
                        break
                    # rolling tail (keep last 16 KB for usage extraction)
                    tail += chunk
                    if len(tail) > 16384:
                        del tail[:-16384]
                if out_filter is not None and not out_filter._closed:
                    tail_emit = out_filter.finish()
                    if tail_emit:
                        writer.write(tail_emit)
                        await writer.drain()
        except httpx.RequestError as e:
            err = b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
            writer.write(err)
            await writer.drain()
            print(f"[proxy] upstream error: {e}", file=sys.stderr)
            return

        ctx.t4 = time.perf_counter()
        elapsed = ctx.t4 - ctx.t0
        pt, ct = _extract_usage(bytes(tail))
        ctx.pt, ctx.ct = pt, ct
        if ct or pt:
            _record(pt, ct, elapsed, ctx.lat())
            tok_s = round(ct / (ctx.t4 - ctx.t3), 1) if ctx.t4 > ctx.t3 else 0
            print(f"[proxy] #{req_id} {pt}in->{ct}out ({tok_s} tok/s) "
                  f"ingress={ctx.t1-ctx.t0:.3f}s opt={ctx.t2-ctx.t1:.3f}s "
                  f"ttft={ctx.t3-ctx.t2:.3f}s gen={ctx.t4-ctx.t3:.3f}s",
                  file=sys.stderr)
        else:
            print(f"[proxy] #{req_id} no-usage ingress={ctx.t1-ctx.t0:.3f}s "
                  f"opt={ctx.t2-ctx.t1:.3f}s ttft={(ctx.t3-ctx.t2) if ctx.t3 else 0:.3f}s",
                  file=sys.stderr)
    except Exception as e:
        print(f"[proxy] handle error: {e}", file=sys.stderr)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ── server ────────────────────────────────────────────────────────────────────
def _maybe_uvloop():
    try:
        import uvloop  # noqa: F401
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        return True
    except Exception:
        return False


def main():
    port = int(os.environ.get("SLIMTOKEN_PORT", os.environ.get("PORT", "8181")))
    upstream = Upstream.from_env()
    uv = _maybe_uvloop()

    limits = httpx.Limits(max_keepalive_connections=64, max_connections=512)
    client = httpx.AsyncClient(
        http2=_bool_env("SLIMTOKEN_HTTP2", False),
        limits=limits, timeout=httpx.Timeout(600.0, connect=10.0),
        trust_env=False)

    req_counter = 0

    async def handler(reader, writer):
        nonlocal req_counter
        req_counter += 1
        await _handle(reader, writer, client, upstream, req_counter)

    async def serve():
        server = await asyncio.start_server(handler, "127.0.0.1", port, backlog=64)
        bound = server.sockets[0].getsockname()
        print(f"[proxy] slimtoken v{__version__} listening on {bound[0]}:{bound[1]} "
              f"-> {upstream.base_url} (tls={upstream.tls}, uvloop={uv}, http2={_bool_env('SLIMTOKEN_HTTP2', False)})",
              file=sys.stderr)
        print(f"[proxy] minify stages: {sorted(_CFG.enabled_stages) or 'OFF'}", file=sys.stderr)
        try:
            async with server:
                await server.serve_forever()
        finally:
            await client.aclose()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        print("[proxy] shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()