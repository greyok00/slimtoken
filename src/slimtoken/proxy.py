"""proxy — the drop-in HTTP(S) optimization proxy.

Point ANTHROPIC_BASE_URL at this proxy (default 127.0.0.1:8181). It:
  1. fully buffers the request body (one JSON object — only the response streams)
  2. strips the `grammar` field (llama-server rejects it; harmless to strip for cloud)
  3. runs the minify pipeline (tools / system / messages, code-fence aware)
  4. forwards to the upstream — raw socket for local http, http.client for cloud https
  5. streams the response back to the client, tracking token usage

No dependency on CortexAgent. The pipeline is the pure stdlib ``minify_request``.
"""
from __future__ import annotations

import json
import os
import re
import select
import socket
import sys
import threading
import time
import errno
from datetime import datetime
from typing import Dict, Optional, Tuple

from .pipeline import minify_request, MinifyConfig
from .upstream import Upstream
from . import __version__

# ── Token tracking ────────────────────────────────────────────────────────────
_token_lock = threading.Lock()
_token_metrics = {
    "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    "requests": 0, "total_time_s": 0.0, "started_at": datetime.now().isoformat(),
    "current_tok_s": 0.0, "avg_tok_s": 0.0,
}


def _record_tokens(pt: int, ct: int, elapsed: float):
    with _token_lock:
        _token_metrics["prompt_tokens"] += pt
        _token_metrics["completion_tokens"] += ct
        _token_metrics["total_tokens"] += pt + ct
        _token_metrics["requests"] += 1
        _token_metrics["total_time_s"] += elapsed
        if elapsed > 0 and ct > 0:
            _token_metrics["current_tok_s"] = round(ct / elapsed, 1)
        if _token_metrics["total_time_s"] > 0 and _token_metrics["completion_tokens"] > 0:
            _token_metrics["avg_tok_s"] = round(
                _token_metrics["completion_tokens"] / _token_metrics["total_time_s"], 1)


def _metrics_json() -> str:
    with _token_lock:
        m = dict(_token_metrics)
    return json.dumps(m, indent=2)


# ── Minify config from env ────────────────────────────────────────────────────
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
    # Master switch OFF = passthrough (everything else defaults ON).
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
    # Budget defaults to a generous backstop: only genuinely bloated contexts
    # get hard-pruned. 0 disables hard-prune entirely (distill still compresses).
    budget = _int_env("SLIMTOKEN_MINIFY_BUDGET", 131072)
    return MinifyConfig(
        token_budget=budget,
        enabled_stages=stages,
        tool_skip=skip,
        keep_last=_int_env("SLIMTOKEN_KEEP_LAST", 8),
        dedup_min_chars=_int_env("SLIMTOKEN_DEDUP_MIN_CHARS", 200),
        distill_max_chars=_int_env("SLIMTOKEN_DISTILL_MAX_CHARS", 240),
    )


_CFG = build_minify_cfg()


def _dechunk(data: bytes):
    """Decode a chunked body. Returns bytes or None if incomplete/malformed."""
    out = b""; i = 0; n = len(data)
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
    return out


class Handler:
    def __init__(self, conn, addr, upstream: Upstream):
        self.conn = conn
        self.addr = addr
        self.upstream = upstream

    def handle(self):
        try:
            req = self._read_request()
            if not req:
                return
            method, path, headers, body = req
            if method == "GET" and path == "/metrics":
                m = _metrics_json().encode()
                head = ("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(m)}\r\n\r\n").encode() + m
                self._write_raw(head)
                return
            if method == "POST":
                self._forward_post(method, path, headers, body)
            else:
                self._forward_passthrough(method, path, headers, body)
        except Exception as e:
            print(f"[proxy] handle error: {e}", file=sys.stderr)
        finally:
            try:
                self.conn.close()
            except Exception:
                pass

    # ── low-level socket IO ────────────────────────────────────────────────────
    def _recv_until(self, marker: bytes, cap: int = 1 << 20) -> Optional[bytes]:
        buf = b""
        while marker not in buf:
            chunk = self.conn.recv(65536)
            if not chunk:
                return None
            buf += chunk
            if len(buf) > cap:
                return None
        return buf

    def _write_raw(self, data: bytes):
        try:
            self.conn.sendall(data)
        except Exception:
            pass

    def _read_request(self) -> Optional[Tuple[str, str, Dict, bytes]]:
        buf = self._recv_until(b"\r\n\r\n")
        if buf is None:
            return None
        head, body = buf.split(b"\r\n\r\n", 1)
        text = head.decode("utf-8", errors="replace")
        lines = text.split("\r\n")
        parts = lines[0].split(" ", 2)
        if len(parts) < 2:
            return None
        method, path = parts[0].upper(), parts[1]
        headers: Dict[str, str] = {}
        order = []
        for line in lines[1:]:
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            headers[k] = v.strip()
            order.append(k)
        # Read full body.
        te = headers.get("transfer-encoding", "").lower()
        cl = headers.get("content-length")
        if "chunked" in te:
            term = b"\r\n0\r\n\r\n"
            while term not in body:
                chunk = self.conn.recv(65536)
                if not chunk:
                    break
                body += chunk
            body = _dechunk(body) or body
        elif cl is not None:
            need = int(cl)
            while len(body) < need:
                chunk = self.conn.recv(min(65536, need - len(body)))
                if not chunk:
                    break
                body += chunk
        return method, path, headers, body

    # ── POST forwarding (minify) ───────────────────────────────────────────────
    def _minify_body(self, body: bytes) -> bytes:
        try:
            parsed = json.loads(body)
        except Exception as e:
            print(f"[proxy] body parse failed (passthrough): {e}", file=sys.stderr)
            return body
        if isinstance(parsed, dict):
            if "grammar" in parsed:
                del parsed["grammar"]
            if _CFG.enabled_stages:
                parsed, stats = minify_request(parsed, _CFG)
                print(f"[proxy] minify: {stats.summary()}", file=sys.stderr)
            return json.dumps(parsed).encode()
        return body

    def _forward_post(self, method, path, headers, body):
        body = self._minify_body(body)
        if self.upstream.tls:
            self._forward_cloud(method, path, headers, body)
        else:
            self._forward_local(method, path, headers, body)

    def _forward_local(self, method, path, headers, body):
        """Raw-socket path for a local http upstream — lowest-latency SSE."""
        # Build the forwarded request.
        out = [f"{method} {path} HTTP/1.1"]
        for k, v in headers.items():
            if k in ("host", "content-length", "transfer-encoding", "expect",
                     "connection"):
                continue
            if k == "user-agent":
                out.append(f"User-Agent: slimtoken/{__version__}")
            else:
                out.append(f"{k}: {v}")
        out.append(f"Host: {self.upstream.host}:{self.upstream.port}")
        out.append(f"Content-Length: {len(body)}")
        out.append("Connection: close")
        head = ("\r\n".join(out) + "\r\n\r\n").encode()
        data = head + body
        try:
            dst = self.upstream.connect_raw()
        except Exception as e:
            self._write_raw(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
            print(f"[proxy] upstream connect failed: {e}", file=sys.stderr)
            return
        try:
            dst.sendall(data)
        except Exception as e:
            print(f"[proxy] upstream send failed: {e}", file=sys.stderr)
            dst.close()
            return
        self._relay_and_track(dst, body)

    def _forward_cloud(self, method, path, headers, body):
        """http.client path for a cloud https upstream (native TLS)."""
        import http.client
        # Sanitize headers for the cloud API.
        fwd = {}
        for k, v in headers.items():
            if k in ("host", "content-length", "transfer-encoding", "expect",
                     "connection"):
                continue
            fwd[k] = v
        fwd["content-length"] = str(len(body))
        conn = self.upstream.https_connection()
        t0 = time.time()
        try:
            conn.request(method, path, body=body, headers=fwd)
            resp = conn.getresponse()
            # Relay status + headers + streamed body to the client.
            status_line = f"HTTP/1.1 {resp.status} {resp.reason}\r\n"
            out_headers = [status_line]
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection"):
                    continue
                out_headers.append(f"{k}: {v}")
            out_headers.append("Connection: close\r\n")
            self._write_raw(("\r\n".join(out_headers) + "\r\n").encode())
            buf = b""
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.conn.sendall(chunk)
                buf += chunk
            self._track_usage(buf, time.time() - t0)
        except Exception as e:
            self._write_raw(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
            print(f"[proxy] cloud forward failed: {e}", file=sys.stderr)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _forward_passthrough(self, method, path, headers, body):
        """Non-POST (e.g. GET /health) — forward without minify."""
        self._forward_local(method, path, headers, body)

    # ── response relay + token tracking ───────────────────────────────────────
    def _relay_and_track(self, dst, req_body):
        stop = threading.Event()
        resp_buf: list = []
        def pump():
            while not stop.is_set():
                r, _, _ = select.select([dst], [], [], 0.3)
                if r:
                    data = dst.recv(65536)
                    if not data:
                        break
                    self.conn.sendall(data)
                    resp_buf.append(data)
        t = threading.Thread(target=pump, daemon=True)
        t.start()
        t0 = time.time()
        try:
            while True:
                r, _, _ = select.select([self.conn], [], [], 0.5)
                if r:
                    data = self.conn.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
        except Exception:
            pass
        finally:
            stop.set()
            t.join(timeout=3)
            self._track_usage(b"".join(resp_buf), time.time() - t0)
            try:
                dst.close()
            except Exception:
                pass

    def _track_usage(self, resp_bytes: bytes, elapsed: float):
        if not resp_bytes:
            return
        text = resp_bytes.decode("utf-8", errors="replace")
        pt, ct = 0, 0
        for line in text.split("\n"):
            if "usage" in line.lower() or "completion_tokens" in line:
                try:
                    if line.startswith("data: "):
                        line = line[6:]
                    usage = json.loads(line).get("usage", {})
                    pt = usage.get("prompt_tokens", 0) or pt
                    ct = usage.get("completion_tokens", 0) or ct
                except Exception:
                    pass
        if ct:
            _record_tokens(pt, ct, elapsed)
            tok_s = round(ct / elapsed, 1) if elapsed > 0 else 0
            print(f"[proxy] tokens: {pt} in -> {ct} out ({tok_s} tok/s, {elapsed:.1f}s)",
                  file=sys.stderr)


def main():
    port = int(os.environ.get("SLIMTOKEN_PORT", os.environ.get("PORT", "8181")))
    upstream = Upstream.from_env()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bound = False
    for attempt in range(20):
        try:
            server.bind(("127.0.0.1", port))
            bound = True
            break
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
            print(f"[proxy] :{port} busy (attempt {attempt+1}/20)", file=sys.stderr)
            time.sleep(0.5)
    if not bound:
        raise OSError(errno.EADDRINUSE, f"port {port} in use")
    server.listen(16)
    print(f"[proxy] slimtoken v{__version__} listening on 127.0.0.1:{port} "
          f"-> {upstream.base_url} (tls={upstream.tls})", file=sys.stderr)
    print(f"[proxy] minify stages: {sorted(_CFG.enabled_stages) or 'OFF'}", file=sys.stderr)
    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=Handler(conn, addr, upstream).handle, daemon=True).start()
    except KeyboardInterrupt:
        print("[proxy] shutting down", file=sys.stderr)
    finally:
        server.close()


if __name__ == "__main__":
    main()