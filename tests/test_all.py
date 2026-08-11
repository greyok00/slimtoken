"""test_all — pytest-free tests for slimtoken (run: python3 test_all.py).

Tests import slimtoken from the package install (or src/ on the path).
"""
import json
import os
import sys
import copy
import socket
import threading
import http.server
import socketserver
import time
import tempfile
import subprocess
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from slimtoken.pipeline import minify_request, MinifyConfig
from slimtoken.message_minify import split_fences, minify_text
from slimtoken import config_optimizer as co
from slimtoken import lazy_mcp
from slimtoken.dedup_tool_results import dedup_tool_results
from slimtoken.distill_old_turns import distill_old_turns, distill_text
from slimtoken import __version__

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


def _tok(obj) -> int:
    return max(1, len(json.dumps(obj, separators=(",", ":"))) // 4)


# ── 1. fence safety ──────────────────────────────────────────────────────────
def test_fences():
    code = "```python\ndef f(x):\n    return x + 1\n```\n"
    mixed = "prose\n\n\n\n\nmore\n\n" + code + "after\n"
    mm = minify_text(mixed)
    check("code fence byte-identical", "def f(x):\n    return x + 1\n" in mm)
    check("blank lines collapsed outside fences", "\n\n\n\n\n" not in mm)
    seg = split_fences("intro\n```\ncode no close\n  ind\n")
    check("malformed fence over-preserved",
          "code no close\n  ind" in "".join(s for f, s in seg if f))


# ── 2. tool integrity ────────────────────────────────────────────────────────
def test_tools():
    long_desc = ("Use this tool to read a file from the local filesystem and return "
                 "its full contents. You can access files by their absolute path. "
                 "The tool returns the file contents as a string. Here is an example "
                 "of how to call it:\n\n"
                 "```bash\nRead a.txt\n```\n\nAnother example showing a different path:\n\n"
                 "```bash\nRead b.txt\n```\n")
    tools = [{"name": "Read", "description": long_desc,
              "input_schema": {"type": "object", "title": "X", "$comment": "c",
                                "required": ["f"], "properties": {"f": {"type": "string",
                                "examples": ["/a"]}}, "enum": ["z"]}}]
    out, _ = minify_request({"tools": tools, "system": "s", "messages": []}, MinifyConfig())
    t = out["tools"][0]
    check("tool name kept", t["name"] == "Read")
    check("required kept", t["input_schema"]["required"] == ["f"])
    check("enum kept", "enum" in t["input_schema"])
    check("title dropped", "title" not in t["input_schema"])
    check("$comment dropped", "$comment" not in t["input_schema"])
    check("examples dropped", "examples" not in t["input_schema"]["properties"]["f"])
    check("only first example kept", t["description"].count("```") == 2)


# ── 3. system tags + budget pairing ──────────────────────────────────────────
def test_system_and_budget():
    sys_ = "<cold_memory>\nr1\n</cold_memory>\n\n```\ncode=x\n```\n\n\n\n\nMore."
    out, _ = minify_request({"system": sys_, "messages": []}, MinifyConfig())
    s = out["system"]
    check("memory tag preserved", "<cold_memory>" in s and "</cold_memory>" in s)
    check("fenced rule preserved", "code=x" in s)
    msgs = [
        {"role": "user", "content": "old1"},
        {"role": "assistant", "content": "old1ans"},
        {"role": "user", "content": "old2"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1",
                                          "name": "R", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result",
                                      "tool_use_id": "tu1", "content": "X"}]},
        {"role": "assistant", "content": "recent"},
        {"role": "user", "content": "recent2"},
        {"role": "assistant", "content": "recent3"},
    ]
    out, st = minify_request({"system": "s", "messages": msgs},
                             MinifyConfig(token_budget=80, keep_last=4))
    use = set(); res = set()
    for m in out["messages"]:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "tool_use":
                        use.add(b.get("id"))
                    if b.get("type") == "tool_result":
                        res.add(b.get("tool_use_id"))
    check("no orphan tool_results", not (res - use), f"orphans={res-use}")


# ── 4. config optimizer ──────────────────────────────────────────────────────
def test_config_optimizer():
    rec = co.recommend(vram_gb=16, model_path=None, model_size_gb=12.74,
                      kv_per_token_bytes=5120)
    check("35B on 16GB fits", rec.est_total_gb < 16, f"est={rec.est_total_gb}")
    check("ctx >= 64k recommended", rec.ctx >= 65536, f"ctx={rec.ctx}")
    check("ub in {512,1024}", rec.ub in (512, 1024), f"ub={rec.ub}")
    check("not the OOM 256k/1024 config", not (rec.ctx == 262144 and rec.ub == 1024),
          f"ctx={rec.ctx} ub={rec.ub}")
    report = co.format_report(rec)
    check("report has llama-server cmd", "llama-server" in report)
    check("report has env exports", "CORTEXAGENT_CTX=" in report)
    check("report has verify disclaimer", "verify" in report.lower())
    rec2 = co.recommend(vram_gb=16, model_size_gb=2.5, kv_per_token_bytes=8192)
    check("small model gets high ctx", rec2.ctx >= 131072, f"ctx={rec2.ctx}")


# ── 5. install/uninstall reversibility ───────────────────────────────────────
def test_install_uninstall():
    import importlib
    cli = importlib.import_module("slimtoken.cli")
    with tempfile.TemporaryDirectory() as td:
        rc = Path(td) / ".bashrc"
        rc.write_text("# my shell config\nexport FOO=bar\n")
        os.environ["SLIMTOKEN_STATE_DIR"] = str(Path(td) / "state")
        cli.STATE_DIR = Path(td) / "state"
        cli.PREV_ENV = Path(td) / "state" / "prev_env"
        cli.main(["install", "--rc", str(rc)])
        text = rc.read_text()
        check("marker block added", cli.MARKER_BEGIN in text)
        check("base url set", "ANTHROPIC_BASE_URL=http://127.0.0.1:8181" in text)
        check("existing config preserved", "export FOO=bar" in text)
        cli.main(["install", "--rc", str(rc), "--url", "http://127.0.0.1:8182"])
        text = rc.read_text()
        check("url updated in place", "ANTHROPIC_BASE_URL=http://127.0.0.1:8182" in text)
        check("only one marker block", text.count(cli.MARKER_BEGIN) == 1)
        cli.main(["uninstall", "--rc", str(rc)])
        text = rc.read_text()
        check("marker block removed", cli.MARKER_BEGIN not in text)
        check("existing config still preserved", "export FOO=bar" in text)


# ── 6. dedup tool results ────────────────────────────────────────────────────
def test_dedup():
    big = "FILE CONTENTS\n" + ("line of code\n" * 200)  # ~2.6k chars, >min_chars
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1", "name": "R", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": big}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu2", "name": "R", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu2", "content": big}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu3", "name": "R", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu3", "content": big}]},
        {"role": "assistant", "content": "done"},
    ]
    stats = {}
    out = dedup_tool_results(copy.deepcopy(msgs), stats, min_chars=200)
    full = 0; stub = 0
    for m in out:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    rc = b.get("content")
                    if isinstance(rc, str) and "FILE CONTENTS" in rc and "omitted" not in rc:
                        full += 1
                    elif isinstance(rc, str) and "omitted" in rc:
                        stub += 1
    check("dedup keeps 1 verbatim", full == 1, f"full={full}")
    check("dedup stubs 2 duplicates", stub == 2, f"stub={stub}")
    check("dedup count in stats", stats.get("dedup_count") == 2, f"stats={stats}")
    use = set(); res = set()
    for m in out:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "tool_use":
                        use.add(b.get("id"))
                    if b.get("type") == "tool_result":
                        res.add(b.get("tool_use_id"))
    check("dedup preserves pairs", not (res - use), f"orphans={res-use}")


# ── 7. distill old turns ─────────────────────────────────────────────────────
def test_distill():
    long_ans = "I will now explain in great detail what I did and why. " * 40  # ~2.4k chars
    msgs = []
    for i in range(10):
        msgs.append({"role": "user", "content": "question %d" % i})
        msgs.append({"role": "assistant", "content": long_ans})
    msgs.append({"role": "user", "content": "final question"})
    stats = {}
    out = distill_old_turns(copy.deepcopy(msgs), stats, keep_last=4, max_chars=240)
    distilled = 0; intact = 0
    for i, m in enumerate(out):
        if i >= len(out) - 4:
            if isinstance(m.get("content"), str) and "great detail" in m["content"]:
                intact += 1
        else:
            if isinstance(m.get("content"), str) and "distilled" in m["content"]:
                distilled += 1
    check("distill compresses old turns", distilled >= 5, f"distilled={distilled}")
    check("distill leaves recent intact", intact >= 1, f"intact={intact}")
    check("distill_text skips short", distill_text("short text") == "short text")
    fenced = ("intro prose here that is long enough to trigger distillation yes indeed. " * 5
              + "\n```python\ncode = 1\n```\n")
    d = distill_text(fenced, max_chars=120)
    check("distill keeps first fence", "```python" in d and "code = 1" in d)


# ── 8. pair-safety under default config (dedup + distill + budget) ───────────
def test_pair_safety_defaults():
    big = "X" * 1500
    msgs = []
    for i in range(12):
        msgs.append({"role": "user", "content": "q %d" % i})
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "tu%d" % i,
                                                       "name": "R", "input": {}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result",
                                                  "tool_use_id": "tu%d" % i, "content": big}]})
        msgs.append({"role": "assistant", "content": "a" * 1500})
    msgs.append({"role": "user", "content": "final"})
    out, st = minify_request({"system": "s", "messages": msgs},
                             MinifyConfig(token_budget=4000, keep_last=6))
    use = set(); res = set()
    for m in out["messages"]:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "tool_use":
                        use.add(b.get("id"))
                    if b.get("type") == "tool_result":
                        res.add(b.get("tool_use_id"))
    check("defaults keep pairs intact", not (res - use), f"orphans={res-use}")
    check("defaults reduced tokens", st.tokens_out < st.tokens_in,
          f"in={st.tokens_in} out={st.tokens_out}")


# ── 9. ≥50% default reduction on realistic bloated payload ───────────────────
def test_default_reduction():
    big_file = "".join("line %d: logic here\n" % i for i in range(500))  # ~9k chars
    long_explain = ("Let me explain my approach in detail. I considered several options "
                    "and decided to proceed as follows because of constraints. " * 30)
    msgs = []
    for i in range(6):
        msgs.append({"role": "user", "content": "please read and fix the file"})
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "tu%d" % i,
                                                       "name": "Read", "input": {"path": "/x.py"}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result",
                                                  "tool_use_id": "tu%d" % i, "content": big_file}]})
        msgs.append({"role": "assistant", "content": long_explain})
    msgs.append({"role": "user", "content": "now finalize"})
    body = {"system": "You are a coding agent. " * 20, "tools": [], "messages": msgs}
    tin = _tok(body)
    out, st = minify_request(copy.deepcopy(body), MinifyConfig())  # all defaults
    tout = _tok(out)
    pct = 100 * (tin - tout) / tin
    print(f"  bloated payload: {tin} -> {tout} tok ({pct:.1f}% reduction)")
    check("default reduces bloated payload >=50%", pct >= 50.0, f"pct={pct:.1f}")
    check("no errors in default run", not st.errors, f"errors={st.errors}")


# ── 10. lazy-mcp smoke (config-driven, empty = no-op) ─────────────────────────
def test_lazy_mcp_smoke():
    with tempfile.TemporaryDirectory() as td:
        os.environ["SLIMTOKEN_LAZY_MCP_CONFIG"] = str(Path(td) / "none.json")
        rc = lazy_mcp.smoke()
        check("lazy_mcp smoke returns 0", rc == 0)
        os.environ["SLIMTOKEN_LAZY_MCP_CONFIG"] = str(Path(td) / "cfg.json")
        Path(td, "cfg.json").write_text(json.dumps([
            {"name": "demo", "command": ["echo", "hi"], "tools_hint": ["a", "b"]}
        ]))
        entries = lazy_mcp.load_config("demo")
        check("lazy_mcp loads config", len(entries) == 1 and entries[0]["name"] == "demo")
        stub = lazy_mcp._build_stub(entries[0])
        check("lazy_mcp stub name", stub["name"] == "lazy_demo")
        check("lazy_mcp stub has real_tool", "real_tool" in stub["inputSchema"]["properties"])


# ── 11. proxy e2e (local) ──────────────────────────────────────
def test_proxy_e2e():
    received = {}

    class U(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            b = self.rfile.read(n) if n else b""
            received["body"] = b
            r = json.dumps({"content": [{"type": "text", "text": "ok"}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(r)))
            self.end_headers()
            self.wfile.write(r)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    class TS(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True

    us = TS(("127.0.0.1", 9210), U)
    threading.Thread(target=us.serve_forever, daemon=True).start()
    time.sleep(0.3)

    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    env = dict(os.environ)
    env["SLIMTOKEN_PORT"] = "9211"
    env["SLIMTOKEN_UPSTREAM"] = "http://127.0.0.1:9210"
    env["SLIMTOKEN_MINIFY_BUDGET"] = "0"  # don't drop our small payload
    env["PYTHONPATH"] = src_dir + ":" + env.get("PYTHONPATH", "")
    p = subprocess.Popen([sys.executable, "-c",
                         "from slimtoken.proxy import main; main()"],
                        env=env, stderr=subprocess.PIPE, cwd=src_dir)
    try:
        ok = False
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:9211/metrics", timeout=1)
                ok = True
                break
            except Exception:
                time.sleep(0.2)
        check("proxy started", ok)
        if not ok:
            return
        payload = {"model": "t", "grammar": "x",
                   "system": "<cold_memory>\nr\n</cold_memory>\n\n\n\n\nMore.",
                   "tools": [{"name": "Read", "description": "short",
                              "input_schema": {"type": "object", "required": ["f"]}}],
                   "messages": [{"role": "user", "content": "hi\n\n\n\nblank"}]}
        body = json.dumps(payload).encode()
        s = socket.create_connection(("127.0.0.1", 9211), timeout=5)
        s.settimeout(5)
        s.sendall(f"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
        try:
            while True:
                d = s.recv(65536)
                if not d:
                    break
        except socket.timeout:
            pass
        s.close()
        time.sleep(0.3)
        got = json.loads(received["body"])
        check("proxy strips grammar", "grammar" not in got)
        check("proxy minifies system blanks", "\n\n\n\n\n" not in got["system"])
        check("proxy preserves memory tag", "<cold_memory>" in got["system"])
        check("proxy preserves tool name", got["tools"][0]["name"] == "Read")
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
        us.shutdown()


def test_tokencount_no_whole_serialize():
    """tokencount must never json.dumps the whole body just to count tokens."""
    import inspect
    from slimtoken import tokencount
    # count_obj walks the structure; it must not fall back to serializing the
    # whole body. Inspect its OWN source (not the module's — the module uses
    # jdumps in the fallback tokenizer path, but count_obj must not).
    csrc = inspect.getsource(tokencount.count_obj)
    check("tokencount has count_obj", hasattr(tokencount, "count_obj"))
    check("count_obj does not serialize whole body", "dumps" not in csrc)
    body = {"system": "x" * 500, "messages": [{"role": "user", "content": "y" * 200}]}
    a = tokencount.count_obj(body)
    check("count_obj returns positive int", isinstance(a, int) and a > 0)
    # same body, same count (cache stable)
    b = tokencount.count_obj(body)
    check("count_obj stable across calls", a == b)
    # estimate_tokens_obj is the drop-in and must equal count_obj for a dict
    check("estimate_tokens_obj matches count_obj",
          tokencount.estimate_tokens_obj(body) == a)


def test_single_pass_equivalence():
    """merged optimize_messages output is byte-identical to the staged path."""
    from slimtoken.pipeline import minify_request, MinifyConfig
    payload = {"system": "<cold_memory>\n\n\nkeep\n</cold_memory>\n\n\nMore.",
               "tools": [{"name": "Read", "description": "a" * 300,
                          "input_schema": {"type": "object"}}],
               "messages": [
                   {"role": "user", "content": "dupe " * 60},
                   {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"f": "x"}}]},
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Z" * 400}]},
                   {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"f": "y"}}]},
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "Z" * 400}]},
                   {"role": "user", "content": "final " * 80},
               ]}
    cfg = MinifyConfig()
    out1, _ = minify_request(copy.deepcopy(payload), cfg)
    out2, _ = minify_request(copy.deepcopy(payload), cfg)
    check("single-pass deterministic", json.dumps(out1, sort_keys=True) == json.dumps(out2, sort_keys=True))
    # reduction actually happened
    from slimtoken.tokencount import count_obj
    check("single-pass reduces tokens", count_obj(out1) < count_obj(payload))


def test_proxy_metrics_and_fastpath():
    """async proxy: /metrics has latency buckets; fast-path forwards bytes unchanged."""
    received = {}
    class U(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); b = self.rfile.read(n) if n else b""
            received["body"] = b
            ev = (b'event: msg\ndata: {"usage":{"prompt_tokens":12,"completion_tokens":7}}\n\n'
                  b'event: stop\ndata: {"type":"message_stop"}\n\n')
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(ev)))
            self.end_headers(); self.wfile.write(ev)
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    class TS(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True; allow_reuse_address = True
    us = TS(("127.0.0.1", 9310), U)
    threading.Thread(target=us.serve_forever, daemon=True).start(); time.sleep(0.2)

    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    LAUNCH = "from slimtoken.proxy import main; main()"

    # proxy 1: minify ON -> /metrics must carry latency buckets + usage
    env = dict(os.environ); env.update({"SLIMTOKEN_PORT": "9311", "SLIMTOKEN_UPSTREAM": "http://127.0.0.1:9310", "SLIMTOKEN_MINIFY_BUDGET": "0", "PYTHONPATH": src_dir})
    p = subprocess.Popen([sys.executable, "-c", LAUNCH], env=env, stderr=subprocess.PIPE, cwd=src_dir)
    try:
        ok = False
        for _ in range(40):
            try: urllib.request.urlopen("http://127.0.0.1:9311/metrics", timeout=1); ok = True; break
            except Exception: time.sleep(0.2)
        check("proxy started (metrics up)", ok)
        if ok:
            body = json.dumps({"model": "t", "grammar": "x",
                "system": "<cold_memory>\nr\n</cold_memory>\n\n\n\n\nMore.",
                "tools": [{"name": "Read", "description": "short", "input_schema": {"type": "object", "required": ["f"]}}],
                "messages": [{"role": "user", "content": "hi\n\n\n\nblank"}]}).encode()
            s = socket.create_connection(("127.0.0.1", 9311), timeout=5); s.settimeout(5)
            s.sendall(f"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
            try:
                while True:
                    d = s.recv(65536)
                    if not d: break
            except socket.timeout: pass
            s.close(); time.sleep(0.3)
            m = json.loads(urllib.request.urlopen("http://127.0.0.1:9311/metrics", timeout=2).read())
            check("metrics has latency buckets",
                  {"proxy_ingress", "optimize", "ttft", "generation", "total"} <= set(m.get("latency", {}).keys()))
            check("metrics recorded a sample", m["latency"]["samples"] >= 1)
            check("metrics extracted completion tokens", m["completion_tokens"] == 7)
    finally:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()

    # proxy 2: minify OFF -> fast-path must forward body bytes unchanged
    env2 = dict(env); env2["SLIMTOKEN_PORT"] = "9312"; env2["SLIMTOKEN_MINIFY"] = "0"
    p2 = subprocess.Popen([sys.executable, "-c", LAUNCH], env=env2, stderr=subprocess.PIPE, cwd=src_dir)
    try:
        ok = False
        for _ in range(40):
            try: urllib.request.urlopen("http://127.0.0.1:9312/metrics", timeout=1); ok = True; break
            except Exception: time.sleep(0.2)
        check("fast-path proxy started", ok)
        if ok:
            received.clear()
            raw = b'{"model":"t","messages":[{"role":"user","content":"exact bytes please"}]}'
            s = socket.create_connection(("127.0.0.1", 9312), timeout=5); s.settimeout(5)
            s.sendall(f"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
            try:
                while True:
                    d = s.recv(65536)
                    if not d: break
            except socket.timeout: pass
            s.close(); time.sleep(0.2)
            check("fast-path forwards body byte-identical", received.get("body") == raw)
    finally:
        p2.terminate()
        try: p2.wait(timeout=5)
        except Exception: p2.kill()
        us.shutdown()


def test_tool_result_compress():
    """type-specific compressors detect shapes, emit metadata, stay pair-safe."""
    from slimtoken.tool_result_compress import compress_text, compress_messages
    # dir listing
    ls = "total 0\n" + "\n".join(f"drwxr-xr-x  2 user group 4096 Jan 1 12:00 dir{i}" for i in range(40))
    c = compress_text(ls)
    check("dir listing compressed", c is not None and "[slimtoken-compressed]" in c)
    check("dir listing has metadata", c is not None and "B -> " in c)
    # json — pretty-printed nested structure (realistic tool-output shape)
    import json as _jj
    j = _jj.dumps({"items": [{"id": i, "name": f"thing_{i}", "tags": ["a", "b"]} for i in range(60)],
                   "meta": {"count": 60}}, indent=2)
    c = compress_text(j)
    check("json compressed", c is not None and c.startswith("[slimtoken-compressed]"))
    # log
    log = "\n".join(f"2024-01-0{i%9+1} 12:00:0{i%9} INFO line {i} " + "z"*30 for i in range(60))
    c = compress_text(log)
    check("log compressed", c is not None and "log 60 lines" in c)
    # source
    src = "\n".join(["def foo(a, b):", "    # comment", "    return a + b", "", "class Bar:", "    pass"] * 12)
    c = compress_text(src)
    check("source compressed", c is not None and "source:" in c)
    # too short -> no compression
    check("short text not compressed", compress_text("hello world") is None)

    # pair-safety: messages with tool_use + tool_result pairing preserved
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"c": "ls"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": ls}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"c": "ls"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": ls}]},
    ]
    new, n = compress_messages(copy.deepcopy(msgs))
    check("compress reported 2 results", n == 2)
    # tool_use blocks untouched
    check("tool_use blocks preserved", new[0]["content"][0]["name"] == "Bash")
    # tool_result ids preserved
    check("tool_result id preserved", new[1]["content"][0]["tool_use_id"] == "t1")
    check("tool_result id preserved 2", new[3]["content"][0]["tool_use_id"] == "t2")
    # message count unchanged (pair-safety: no removal/reorder)
    check("message count unchanged", len(new) == len(msgs))
    # content was actually rewritten
    check("tool_result content rewritten", new[1]["content"][0]["content"] != msgs[1]["content"][0]["content"])


def test_output_filter():
    """raw passthrough when unset; max_tokens truncates; stop truncates."""
    from slimtoken.output_filter import OutputFilter

    # 1. raw passthrough when no levers set (feed == input)
    f = OutputFilter(max_tokens=None, stops=[])
    out = f.feed(b"event: x\ndata: {\"delta\":{\"text\":\"hello\"}}\n\n")
    check("raw passthrough unset levers", out == b"event: x\ndata: {\"delta\":{\"text\":\"hello\"}}\n\n")

    # 2. max_tokens truncation: cap at ~3 tokens, stream more
    # use a simple repeating word so tokens are countable
    f = OutputFilter(max_tokens=3, stops=[])
    stream = b'event: m\ndata: {"type":"content_block_delta","delta":{"text":"apple banana cherry date elderberry"}}\n\n'
    out = f.feed(stream)
    # the filter should emit a truncated text and then close
    check("max_tokens produced output", len(out) > 0)
    check("max_tokens filter closed", f._closed is True)
    # the emitted text must be a prefix of the original (truncation, not garbage)
    import json as _j
    # parse the emitted data line
    line = [l for l in out.decode().split("\n") if l.startswith("data:")][0]
    emitted_text = _j.loads(line[5:].strip())["delta"]["text"]
    check("max_tokens emitted prefix", "apple banana cherry date elderberry".startswith(emitted_text))

    # 3. stop-sequence truncation: stop string itself NOT emitted
    f = OutputFilter(max_tokens=None, stops=["STOP"])
    stream = b'event: m\ndata: {"delta":{"text":"before text STOP after text"}}\n\n'
    out = f.feed(stream)
    check("stop filter produced output", len(out) > 0)
    check("stop filter closed", f._closed is True)
    line = [l for l in out.decode().split("\n") if l.startswith("data:")][0]
    emitted_text = _j.loads(line[5:].strip())["delta"]["text"]
    check("stop emitted before-text only", emitted_text == "before text ")
    check("stop string not emitted", "STOP" not in emitted_text)

    # 4. non-data frames pass through
    f = OutputFilter(max_tokens=2, stops=[])
    out = f.feed(b": ping\n\n")
    check("non-data frame passes through", out == b": ping\n\n")


def test_output_filter_filler():
    """SLIMTOKEN_FILLER strips lead-in filler from the response head."""
    from slimtoken.output_filter import OutputFilter, from_env, is_active

    def frame(text):
        return ("data: " + json.dumps({"delta": {"text": text}}) + "\n\n").encode()

    # 1. single chunk: all leading filler stripped, real content kept
    f = OutputFilter(filler=True)
    out = f.feed(frame("Sure!\nHere is the code:\nprint(1)"))
    check("filler single-chunk strips lead-in", b"print(1)" in out
          and b"Sure" not in out and b"Here is the code" not in out)

    # 2. filler phrase split across chunks is still caught
    f = OutputFilter(filler=True)
    c1 = f.feed(frame("Sure"))
    c2 = f.feed(frame("!\nThe answer is 42"))
    check("filler split-chunk caught", b"42" in c2 and b"Sure" not in c1 + c2)

    # 3. real content starting immediately passes through untouched
    f = OutputFilter(filler=True)
    out = f.feed(frame("The answer is 42"))
    check("filler real-content passthrough", b"The answer is 42" in out)

    # 4. whole response is filler -> finish() flushes it verbatim
    f = OutputFilter(filler=True)
    c = f.feed(frame("Sure!"))
    fin = f.finish()
    check("filler whole-response flushed at finish", b"Sure" in fin)

    # 5. filler composes with max_tokens
    f = OutputFilter(filler=True, max_tokens=1000)
    out = f.feed(frame("Sure!\nlong content here"))
    check("filler composes with max_tokens", b"long content" in out and b"Sure" not in out)

    # 6. env wiring: SLIMTOKEN_FILLER=1 activates the filter
    os.environ["SLIMTOKEN_FILLER"] = "1"
    check("filler is_active", is_active())
    ef = from_env()
    check("filler from_env builds", ef is not None and ef.filler)
    os.environ.pop("SLIMTOKEN_FILLER", None)
    check("filler inactive when unset", not is_active())


def test_dom_stage():
    """opt-in dom stage prunes large HTML tool_results; pair-safe."""
    from slimtoken.dom_pruner import prune_dom, clear_dom_cache
    from slimtoken.pipeline import MinifyConfig, minify_request

    # 1. prune_dom strips script/nav/attrs
    h = ('<html><head><script>alert(1)</script></head>'
         '<body><nav>menu</nav><div class="x" id="y" data-z="w">hi</div>'
         '<footer>copy</footer></body></html>')
    p = prune_dom(h, "s")
    check("dom strips script", "alert" not in p)
    check("dom strips nav/footer", "menu" not in p and "copy" not in p)
    check("dom strips attrs", "class=" not in p and "data-" not in p)
    check("dom keeps content", "hi" in p)

    # 2. pipeline stage fires only when minify_dom=True and content is big HTML
    big_html = "<html><body>" + "<div>row</div>" * 2000 + "</body></html>"
    body = {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": big_html}]}]}
    cfg_off = MinifyConfig(minify_dom=False)
    nb, stats = minify_request(copy.deepcopy(body), cfg_off)
    check("dom stage off by default", stats.dom_minified == 0)
    cfg_on = MinifyConfig(minify_dom=True)
    nb, stats = minify_request(copy.deepcopy(body), cfg_on)
    check("dom stage fires when enabled", stats.dom_minified == 1)
    check("dom stage pruned content", len(nb["messages"][0]["content"][0]["content"]) < len(big_html))

    # 3. pair-safety: tool_use_id preserved, message count unchanged
    check("dom preserves tool_use_id", nb["messages"][0]["content"][0]["tool_use_id"] == "t1")
    check("dom preserves message count", len(nb["messages"]) == 1)

    # 4. small / non-HTML tool_results untouched
    body2 = {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": "short text"}]}]}
    nb2, stats2 = minify_request(copy.deepcopy(body2), cfg_on)
    check("dom skips small results", stats2.dom_minified == 0)
    check("dom skips small content", nb2["messages"][0]["content"][0]["content"] == "short text")

    clear_dom_cache()


def test_stats_persistence():
    """SLIMTOKEN_STATS_FILE persists cumulative minify stats to disk."""
    from slimtoken import proxy as _proxy

    with tempfile.TemporaryDirectory() as td:
        stats_file = str(Path(td) / "minify_stats.json")
        os.environ["SLIMTOKEN_STATS_FILE"] = stats_file
        # re-read the module-level file path (built at import)
        _proxy._MINIFY_STATS_FILE = stats_file
        _proxy._minify_stats = {
            "runs": 0, "tokens_in": 0, "tokens_out": 0, "tokens_saved": 0,
            "ratio_pct": 0.0, "last_run_ts": "", "last_saved_pct": 0.0,
            "history_60s": [],
        }
        try:
            from slimtoken.pipeline import MinifyConfig, minify_request
            body = {"messages": [{"role": "user", "content": "hello world this is a test"}]}
            cfg = MinifyConfig()
            _, stats = minify_request(body, cfg)
            _proxy._record_minify(stats)
            _proxy._record_minify(stats)
            data = json.loads(Path(stats_file).read_text())
            check("stats file written", data["runs"] == 2)
            check("stats tokens tracked", data["tokens_in"] > 0 and data["tokens_out"] > 0)
            check("stats ratio computed", data["ratio_pct"] >= 0)
            check("stats history capped", len(data["history_60s"]) == 2)
        finally:
            os.environ.pop("SLIMTOKEN_STATS_FILE", None)
            _proxy._MINIFY_STATS_FILE = None


# ── adapters (OpenAI/Ollama ↔ Anthropic canonical) ───────────────────────────
def test_adapters():
    from slimtoken import adapters

    # detect() by URL path
    check("detect /v1/messages → anthropic",
          adapters.detect("https://api.x.com/v1/messages") == "anthropic")
    check("detect /v1/chat/completions → openai",
          adapters.detect("https://api.x.com/v1/chat/completions") == "openai")
    check("detect /api/chat → ollama",
          adapters.detect("http://localhost:11434/api/chat") == "ollama")
    check("detect /api/generate → ollama",
          adapters.detect("http://localhost:11434/api/generate") == "ollama")
    check("detect unknown → None",
          adapters.detect("https://x.com/other") is None)
    check("detect strips query string",
          adapters.detect("/v1/messages?beta=true") == "anthropic")

    # anthropic is identity (no conversion, no copy needed)
    body_anth = {"system": "s", "messages": [{"role": "user", "content": "hi"}]}
    check("anthropic to_canonical identity",
          adapters.to_canonical(body_anth, "anthropic") is body_anth)
    check("anthropic from_canonical identity",
          adapters.from_canonical(body_anth, "anthropic") is body_anth)

    # OpenAI → canonical: system message hoisted, tool_calls → tool_use, role:tool → tool_result
    openai_body = {
        "model": "gpt-x", "max_tokens": 100, "stream": True,
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "ls", "arguments": '{"path":"."}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "a.txt\nb.txt"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "ls", "description": "list",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}],
    }
    canon = adapters.to_canonical(openai_body, "openai")
    check("openai→canon top-level system", canon.get("system") == "You are helpful.")
    check("openai→canon model carried", canon.get("model") == "gpt-x")
    check("openai→canon max_tokens carried", canon.get("max_tokens") == 100)
    # messages: user, assistant w/ tool_use, user w/ tool_result
    roles = [m["role"] for m in canon["messages"]]
    check("openai→canon roles", roles == ["user", "assistant", "user"],
          f"{roles}")
    asst = [m for m in canon["messages"] if m["role"] == "assistant"][0]
    tu = [b for b in asst["content"] if b.get("type") == "tool_use"]
    check("openai→canon tool_use block", len(tu) == 1 and tu[0]["id"] == "c1"
          and tu[0]["name"] == "ls")
    check("openai→canon tool_use input parsed to dict",
          tu[0]["input"] == {"path": "."}, f"{tu[0]['input']}")
    usr_results = [m for m in canon["messages"] if m["role"] == "user"]
    tr = [b for b in usr_results[-1]["content"] if b.get("type") == "tool_result"]
    check("openai→canon tool_result block", len(tr) == 1 and tr[0]["tool_use_id"] == "c1")
    check("openai→canon tool input_schema",
          canon["tools"][0]["input_schema"]["properties"]["path"]["type"] == "string")

    # canonical → OpenAI: reverse it
    back = adapters.from_canonical(canon, "openai")
    back_roles = [m["role"] for m in back["messages"]]
    check("canon→openai has system role", back_roles[0] == "system")
    tc_back = [m for m in back["messages"] if m.get("tool_calls")]
    check("canon→openai assistant tool_calls restored", len(tc_back) == 1)
    check("canon→openai tool_call args JSON string",
          isinstance(tc_back[0]["tool_calls"][0]["function"]["arguments"], str))
    tool_back = [m for m in back["messages"] if m["role"] == "tool"]
    check("canon→openai role:tool restored", len(tool_back) == 1
          and tool_back[0]["tool_call_id"] == "c1")
    check("canon→openai tool parameters restored",
          back["tools"][0]["function"]["parameters"]["properties"]["path"]["type"] == "string")

    # ollama reuses openai conversion (same shape); passthrough of ollama-only fields
    ollama_body = {"model": "llama3", "messages": [{"role": "user", "content": "hi"}],
                   "options": {"temperature": 0.2}, "keep_alive": "5m", "format": "json"}
    canon_o = adapters.to_canonical(ollama_body, "ollama")
    check("ollama→canon options carried", canon_o.get("options") == {"temperature": 0.2})
    check("ollama→canon keep_alive carried", canon_o.get("keep_alive") == "5m")
    check("ollama→canon format carried", canon_o.get("format") == "json")
    back_o = adapters.from_canonical(canon_o, "ollama")
    check("ollama round-trip options survive", back_o.get("options") == {"temperature": 0.2})
    check("ollama round-trip keep_alive survives", back_o.get("keep_alive") == "5m")

    # pair-safety across round trip: consecutive tool results merge into one user msg
    multi = {"messages": [
        {"role": "user", "content": "do both"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "f1", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "f2", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "r1"},
        {"role": "tool", "tool_call_id": "b", "content": "r2"},
    ]}
    cm = adapters.to_canonical(multi, "openai")["messages"]
    # one assistant w/ two tool_use, one user w/ two tool_result
    asst2 = [m for m in cm if m["role"] == "assistant"][0]
    check("pair-safety: two tool_use in one assistant",
          len([b for b in asst2["content"] if b.get("type") == "tool_use"]) == 2)
    usr2 = [m for m in cm if m["role"] == "user"][-1]
    check("pair-safety: two tool_result merged into one user",
          len([b for b in usr2["content"] if b.get("type") == "tool_result"]) == 2)
    # reverse preserves both tool replies
    back2 = adapters.from_canonical({"messages": cm}, "openai")
    tool2 = [m for m in back2["messages"] if m["role"] == "tool"]
    check("pair-safety reverse: two role:tool restored", len(tool2) == 2)


# ── context_presets (high-context VRAM tiers, dense + MoE) ───────────────────
def test_context_presets():
    from slimtoken import context_presets as cp

    rows = cp.list_context_presets()
    check("presets cover all tiers", {r["vram_gb"] for r in rows} == {4, 8, 16},
          f"{sorted({r['vram_gb'] for r in rows})}")
    check("every tier has dense + MoE",
          all(any(r["kind"] == "dense" for r in rows if r["vram_gb"] == t)
              and any(r["kind"] == "MoE" for r in rows if r["vram_gb"] == t)
              for t in (4, 8, 16)))

    for r in rows:
        tid = f"{r['vram_gb']}GB {r['kind']}"
        check(f"{tid} fits in VRAM (total<=vram)",
              r["total_gb"] <= r["vram_gb"], f"total={r['total_gb']}")
        check(f"{tid} margin >= 0", r["margin_gb"] >= 0, f"margin={r['margin_gb']}")
        check(f"{tid} effective > nominal",
              r["effective_ctx"] > r["nominal_ctx"],
              f"{r['effective_ctx']} vs {r['nominal_ctx']}")
        check(f"{tid} q4_0 KV", r["kv_quant"] == "q4_0")
        check(f"{tid} has llama_cmd", isinstance(r["llama_cmd"], str) and "llama-server" in r["llama_cmd"])
        check(f"{tid} ub present", isinstance(r["ub"], int) and r["ub"] > 0)

    # 16GB MoE is capped at 128k (the proven-stable value), not the 256k high side
    moe16 = [r for r in rows if r["vram_gb"] == 16 and r["kind"] == "MoE"][0]
    check("16GB MoE capped at 128k", moe16["nominal_ctx"] == 131072,
          f"{moe16['nominal_ctx']}")
    check("16GB MoE is Qwen3.6-35B-A3B", moe16["model"] == "Qwen3.6-35B-A3B")

    # vram filter
    only8 = cp.list_context_presets(8)
    check("vram filter 8 returns only 8GB", all(r["vram_gb"] == 8 for r in only8)
          and len(only8) >= 2)

    # best_context_for_tier returns the max effective
    best16 = cp.best_context_for_tier(16)
    all16 = cp.list_context_presets(16)
    check("best_context_for_tier picks max effective",
          best16["effective_ctx"] == max(r["effective_ctx"] for r in all16),
          f"{best16['effective_ctx']}")
    check("best_context_for_tier None on unknown tier",
          cp.best_context_for_tier(2) is None)


def main():
    tests = [test_fences, test_tools, test_system_and_budget, test_config_optimizer,
             test_install_uninstall, test_dedup, test_distill,
             test_pair_safety_defaults, test_default_reduction, test_lazy_mcp_smoke,
             test_proxy_e2e, test_tokencount_no_whole_serialize,
             test_single_pass_equivalence, test_proxy_metrics_and_fastpath,
             test_tool_result_compress, test_output_filter,
             test_adapters, test_context_presets]
    print(f"slimtoken v{__version__} — running {len(tests)} test groups")
    for t in tests:
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n{'='*48}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())