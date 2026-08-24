
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



def test_fences():
    code = "```python\ndef f(x):\n    return x + 1\n```\n"
    mixed = "prose\n\n\n\n\nmore\n\n" + code + "after\n"
    mm = minify_text(mixed)
    check("code fence byte-identical", "def f(x):\n    return x + 1\n" in mm)
    check("blank lines collapsed outside fences", "\n\n\n\n\n" not in mm)
    seg = split_fences("intro\n```\ncode no close\n  ind\n")
    check("malformed fence over-preserved",
          "code no close\n  ind" in "".join(s for f, s in seg if f))



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



def test_dedup():
    big = "FILE CONTENTS\n" + ("line of code\n" * 200)
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



def test_distill():
    long_ans = "I will now explain in great detail what I did and why. " * 40
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



def test_default_reduction():
    big_file = "".join("line %d: logic here\n" % i for i in range(500))
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
    out, st = minify_request(copy.deepcopy(body), MinifyConfig())
    tout = _tok(out)
    pct = 100 * (tin - tout) / tin
    print(f"  bloated payload: {tin} -> {tout} tok ({pct:.1f}% reduction)")
    check("default reduces bloated payload >=50%", pct >= 50.0, f"pct={pct:.1f}")
    check("no errors in default run", not st.errors, f"errors={st.errors}")



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
    env["SLIMTOKEN_MINIFY_BUDGET"] = "0"
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

    import inspect
    from slimtoken import tokencount



    csrc = inspect.getsource(tokencount.count_obj)
    check("tokencount has count_obj", hasattr(tokencount, "count_obj"))
    check("count_obj does not serialize whole body", "dumps" not in csrc)
    body = {"system": "x" * 500, "messages": [{"role": "user", "content": "y" * 200}]}
    a = tokencount.count_obj(body)
    check("count_obj returns positive int", isinstance(a, int) and a > 0)

    b = tokencount.count_obj(body)
    check("count_obj stable across calls", a == b)

    check("estimate_tokens_obj matches count_obj",
          tokencount.estimate_tokens_obj(body) == a)


def test_single_pass_equivalence():

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

    from slimtoken.tokencount import count_obj
    check("single-pass reduces tokens", count_obj(out1) < count_obj(payload))


def test_proxy_metrics_and_fastpath():

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

    from slimtoken.tool_result_compress import compress_text, compress_messages

    ls = "total 0\n" + "\n".join(f"drwxr-xr-x  2 user group 4096 Jan 1 12:00 dir{i}" for i in range(40))
    c = compress_text(ls)
    check("dir listing compressed", c is not None and "[slimtoken-compressed]" in c)
    check("dir listing has metadata", c is not None and "B -> " in c)

    import json as _jj
    j = _jj.dumps({"items": [{"id": i, "name": f"thing_{i}", "tags": ["a", "b"]} for i in range(60)],
                   "meta": {"count": 60}}, indent=2)
    c = compress_text(j)
    check("json compressed", c is not None and c.startswith("[slimtoken-compressed]"))

    log = "\n".join(f"2024-01-0{i%9+1} 12:00:0{i%9} INFO line {i} " + "z"*30 for i in range(60))
    c = compress_text(log)
    check("log compressed", c is not None and "log 60 lines" in c)

    src = "\n".join(["def foo(a, b):", "    # comment", "    return a + b", "", "class Bar:", "    pass"] * 12)
    c = compress_text(src)
    check("source compressed", c is not None and "source:" in c)

    check("short text not compressed", compress_text("hello world") is None)


    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"c": "ls"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": ls}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"c": "ls"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": ls}]},
    ]
    new, n = compress_messages(copy.deepcopy(msgs))
    check("compress reported 2 results", n == 2)

    check("tool_use blocks preserved", new[0]["content"][0]["name"] == "Bash")

    check("tool_result id preserved", new[1]["content"][0]["tool_use_id"] == "t1")
    check("tool_result id preserved 2", new[3]["content"][0]["tool_use_id"] == "t2")

    check("message count unchanged", len(new) == len(msgs))

    check("tool_result content rewritten", new[1]["content"][0]["content"] != msgs[1]["content"][0]["content"])


def test_output_filter():

    from slimtoken.output_filter import OutputFilter


    f = OutputFilter(max_tokens=None, stops=[], filler=False)
    out = f.feed(b"event: x\ndata: {\"delta\":{\"text\":\"hello\"}}\n\n")
    check("raw passthrough all levers off", out == b"event: x\ndata: {\"delta\":{\"text\":\"hello\"}}\n\n")



    f = OutputFilter(max_tokens=3, stops=[], filler=False)
    stream = b'event: m\ndata: {"type":"content_block_delta","delta":{"text":"apple banana cherry date elderberry"}}\n\n'
    out = f.feed(stream)

    check("max_tokens produced output", len(out) > 0)
    check("max_tokens filter closed", f._closed is True)

    import json as _j

    line = [l for l in out.decode().split("\n") if l.startswith("data:")][0]
    emitted_text = _j.loads(line[5:].strip())["delta"]["text"]
    check("max_tokens emitted prefix", "apple banana cherry date elderberry".startswith(emitted_text))


    f = OutputFilter(max_tokens=None, stops=["STOP"], filler=False)
    stream = b'event: m\ndata: {"delta":{"text":"before text STOP after text"}}\n\n'
    out = f.feed(stream)
    check("stop filter produced output", len(out) > 0)
    check("stop filter closed", f._closed is True)
    line = [l for l in out.decode().split("\n") if l.startswith("data:")][0]
    emitted_text = _j.loads(line[5:].strip())["delta"]["text"]
    check("stop emitted before-text only", emitted_text == "before text ")
    check("stop string not emitted", "STOP" not in emitted_text)


    f = OutputFilter(max_tokens=2, stops=[], filler=False)
    out = f.feed(b": ping\n\n")
    check("non-data frame passes through", out == b": ping\n\n")


def test_output_filter_filler():

    from slimtoken.output_filter import OutputFilter, from_env, is_active

    def frame(text):
        return ("data: " + json.dumps({"delta": {"text": text}}) + "\n\n").encode()


    f = OutputFilter(filler=True)
    out = f.feed(frame("Sure!\nHere is the code:\nprint(1)"))
    check("filler single-chunk strips lead-in", b"print(1)" in out
          and b"Sure" not in out and b"Here is the code" not in out)


    f = OutputFilter(filler=True)
    c1 = f.feed(frame("Sure"))
    c2 = f.feed(frame("!\nThe answer is 42"))
    check("filler split-chunk caught", b"42" in c2 and b"Sure" not in c1 + c2)


    f = OutputFilter(filler=True)
    out = f.feed(frame("The answer is 42"))
    check("filler real-content passthrough", b"The answer is 42" in out)


    f = OutputFilter(filler=True)
    c = f.feed(frame("Sure!"))
    fin = f.finish()
    check("filler whole-response flushed at finish", b"Sure" in fin)


    f = OutputFilter(filler=True, max_tokens=1000)
    out = f.feed(frame("Sure!\nlong content here"))
    check("filler composes with max_tokens", b"long content" in out and b"Sure" not in out)


    os.environ.pop("SLIMTOKEN_FILLER", None)
    check("filler active by default", is_active())
    ef = from_env()
    check("filler from_env builds on by default", ef is not None and ef.filler)


    os.environ["SLIMTOKEN_FILLER"] = "0"
    check("filler inactive when SLIMTOKEN_FILLER=0", not is_active())
    ef = from_env()
    check("filler from_env None when disabled", ef is None)
    os.environ.pop("SLIMTOKEN_FILLER", None)


def test_dom_stage():

    from slimtoken.dom_pruner import prune_dom, clear_dom_cache
    from slimtoken.pipeline import MinifyConfig, minify_request


    h = ('<html><head><script>alert(1)</script></head>'
         '<body><nav>menu</nav><div class="x" id="y" data-z="w">hi</div>'
         '<footer>copy</footer></body></html>')
    p = prune_dom(h, "s")
    check("dom strips script", "alert" not in p)
    check("dom strips nav/footer", "menu" not in p and "copy" not in p)
    check("dom strips attrs", "class=" not in p and "data-" not in p)
    check("dom keeps content", "hi" in p)


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


    check("dom preserves tool_use_id", nb["messages"][0]["content"][0]["tool_use_id"] == "t1")
    check("dom preserves message count", len(nb["messages"]) == 1)


    body2 = {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": "short text"}]}]}
    nb2, stats2 = minify_request(copy.deepcopy(body2), cfg_on)
    check("dom skips small results", stats2.dom_minified == 0)
    check("dom skips small content", nb2["messages"][0]["content"][0]["content"] == "short text")

    clear_dom_cache()


def test_stats_persistence():

    from slimtoken import proxy as _proxy

    with tempfile.TemporaryDirectory() as td:
        stats_file = str(Path(td) / "minify_stats.json")
        os.environ["SLIMTOKEN_STATS_FILE"] = stats_file

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



def test_adapters():
    from slimtoken import adapters


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


    body_anth = {"system": "s", "messages": [{"role": "user", "content": "hi"}]}
    check("anthropic to_canonical identity",
          adapters.to_canonical(body_anth, "anthropic") is body_anth)
    check("anthropic from_canonical identity",
          adapters.from_canonical(body_anth, "anthropic") is body_anth)


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


    ollama_body = {"model": "llama3", "messages": [{"role": "user", "content": "hi"}],
                   "options": {"temperature": 0.2}, "keep_alive": "5m", "format": "json"}
    canon_o = adapters.to_canonical(ollama_body, "ollama")
    check("ollama→canon options carried", canon_o.get("options") == {"temperature": 0.2})
    check("ollama→canon keep_alive carried", canon_o.get("keep_alive") == "5m")
    check("ollama→canon format carried", canon_o.get("format") == "json")
    back_o = adapters.from_canonical(canon_o, "ollama")
    check("ollama round-trip options survive", back_o.get("options") == {"temperature": 0.2})
    check("ollama round-trip keep_alive survives", back_o.get("keep_alive") == "5m")


    multi = {"messages": [
        {"role": "user", "content": "do both"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "f1", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "f2", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "r1"},
        {"role": "tool", "tool_call_id": "b", "content": "r2"},
    ]}
    cm = adapters.to_canonical(multi, "openai")["messages"]

    asst2 = [m for m in cm if m["role"] == "assistant"][0]
    check("pair-safety: two tool_use in one assistant",
          len([b for b in asst2["content"] if b.get("type") == "tool_use"]) == 2)
    usr2 = [m for m in cm if m["role"] == "user"][-1]
    check("pair-safety: two tool_result merged into one user",
          len([b for b in usr2["content"] if b.get("type") == "tool_result"]) == 2)

    back2 = adapters.from_canonical({"messages": cm}, "openai")
    tool2 = [m for m in back2["messages"] if m["role"] == "tool"]
    check("pair-safety reverse: two role:tool restored", len(tool2) == 2)



def test_distill_user_preserved():

    req = ("Requirement 1: connect to the postgres DB.\n"
           "Requirement 2: run the migration on schema public.\n"
           "Requirement 3: keep all rows in the audit table.\n"
           "Requirement 4: export results to csv.\n" * 20)
    msgs = []
    for _i in range(5):
        msgs.append({"role": "user", "content": req})
        msgs.append({"role": "assistant",
                     "content": "I will explain my approach in very great detail. " * 40})
    msgs.append({"role": "user", "content": "final"})


    stats = {}
    out = distill_old_turns(copy.deepcopy(msgs), stats, keep_last=4, max_chars=160)
    user_full = sum(1 for m in out
                    if m.get("role") == "user" and isinstance(m.get("content"), str)
                    and "Requirement 1:" in m["content"] and "Requirement 4:" in m["content"])
    asst_distilled = sum(1 for m in out
                         if m.get("role") == "assistant" and isinstance(m.get("content"), str)
                         and "distilled" in m["content"])
    check("audit1 default: old user requirements survive", user_full >= 4, f"user_full={user_full}")
    check("audit1 default: assistant prose distilled", asst_distilled >= 2, f"asst={asst_distilled}")


    stats2 = {}
    out2 = distill_old_turns(copy.deepcopy(msgs), stats2, keep_last=4, max_chars=160,
                             include_user=True)
    user_distilled = sum(1 for m in out2
                         if m.get("role") == "user" and isinstance(m.get("content"), str)
                         and "distilled" in m["content"])
    check("audit1 opt-in compresses user turns", user_distilled >= 2, f"{user_distilled}")


    body = {"system": "s", "messages": msgs}
    nb, _ = minify_request(copy.deepcopy(body), MinifyConfig(keep_last=4))
    user_req = sum(1 for m in nb["messages"]
                   if m.get("role") == "user" and isinstance(m.get("content"), str)
                   and "Requirement 4:" in m["content"])
    check("audit1 pipeline default: old user requirements survive", user_req >= 4, f"{user_req}")


    body2 = {"system": "s", "messages": msgs}
    nb2, _ = minify_request(copy.deepcopy(body2), MinifyConfig(keep_last=4,
                                                               distill_include_user=True))
    user_dist2 = sum(1 for m in nb2["messages"]
                     if m.get("role") == "user" and isinstance(m.get("content"), str)
                     and "distilled" in m["content"])
    check("audit1 pipeline opt-in compresses user turns", user_dist2 >= 2, f"{user_dist2}")



def test_json_tail_recall():

    from slimtoken.tool_result_compress import compress_text
    import json as _jj
    records = [{"id": i, "payload": "x" * 80} for i in range(500)]
    pretty = _jj.dumps(records, indent=2)
    c = compress_text(pretty)
    check("audit2 json compressed", c is not None)
    body = c.split("json: ", 1)[1]
    check("audit2 json tail record survives", '"id":499' in body, "TAIL LOST")
    check("audit2 json head record survives", '"id":0' in body and '"id":2' in body)
    check("audit2 json tail slice intact", '"id":497' in body and '"id":498' in body)
    check("audit2 json omission marker present", "omitted" in body)
    check("audit2 json not full dump", '"id":250' not in body)



    mid = _jj.dumps([{"id": i, "k": "v"} for i in range(30)], indent=2)
    cm = compress_text(mid)
    check("audit2 mid json emitted whole", cm is not None
          and '"id":29' in cm and "omitted" not in cm)


    tiny = _jj.dumps([{"id": 1}, {"id": 2}], indent=2)
    ct = compress_text(tiny)
    check("audit2 tiny json untouched (zero loss)", ct is None)


def test_source_tail_recall():

    from slimtoken.tool_result_compress import compress_text
    lines = []
    for i in range(200):
        lines.append(f"def func_{i}():")
        lines.append(f"    return {i}")
    src = "\n".join(lines)
    c = compress_text(src)
    check("audit2 source compressed", c is not None and "source:" in c)
    check("audit2 source tail survives", "func_199" in c, "TAIL LOST")
    check("audit2 source head survives", "func_0" in c)
    check("audit2 source omission marker", "omitted" in c)
    check("audit2 source not full dump", "func_100" not in c)



def test_openai_multimodal_roundtrip():

    from slimtoken import adapters
    body = {"messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {"type": "input_audio", "input_audio": {"data": "BBB", "format": "wav"}},
        ]},
    ]}
    canon = adapters.to_canonical(body, "openai")
    cj = json.dumps(canon)
    check("audit3 canon keeps image", "image_url" in cj and "data:image/png;base64,AAA" in cj)
    check("audit3 canon keeps audio", "input_audio" in cj and "BBB" in cj)
    check("audit3 canon keeps text", "describe this" in cj)
    back = adapters.from_canonical(canon, "openai")
    bj = json.dumps(back)
    check("audit3 round-trip keeps image", "image_url" in bj and "data:image/png;base64,AAA" in bj)
    check("audit3 round-trip keeps audio", "input_audio" in bj and "BBB" in bj)
    check("audit3 round-trip keeps text", "describe this" in bj)


def test_output_filter_openai_delta():

    from slimtoken.output_filter import OutputFilter


    f = OutputFilter(max_tokens=3, stops=[], filler=False)
    frame = ('data: ' + json.dumps(
        {"choices": [{"delta": {"content": "apple banana cherry date"}}]}) + '\n\n').encode()
    out = f.feed(frame)
    line = [l for l in out.decode().split("\n") if l.startswith("data:")][0]
    obj = json.loads(line[5:].strip())
    content = obj["choices"][0]["delta"]["content"]
    check("audit3 openai string delta.content truncated",
          isinstance(content, str)
          and "apple banana cherry date".startswith(content)
          and len(content) < len("apple banana cherry date"))
    check("audit3 openai string filter closed", f._closed)


    f = OutputFilter(max_tokens=None, stops=["STOP"], filler=False)
    frame = ('data: ' + json.dumps({"choices": [{"delta": {"content": [
                {"type": "text", "text": "keep me"},
                {"type": "text", "text": " STOP rest"}]}}]}) + '\n\n').encode()
    out = f.feed(frame)
    line = [l for l in out.decode().split("\n") if l.startswith("data:")][0]
    obj = json.loads(line[5:].strip())
    blocks = obj["choices"][0]["delta"]["content"]
    texts = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    check("audit3 openai block-list stop truncates", "STOP" not in texts and "keep me" in texts)
    check("audit3 openai block-list stop closes", f._closed)


    f = OutputFilter(filler=True)
    frame = ('data: ' + json.dumps({"choices": [{"delta": {
        "tool_calls": [{"id": "t", "type": "function",
                        "function": {"name": "ls", "arguments": "{}"}}]}}]}) + '\n\n').encode()
    out = f.feed(frame)
    check("audit3 openai non-text delta passthrough", out == frame)



def test_uninstall_no_duplicate():

    import importlib
    cli = importlib.import_module("slimtoken.cli")
    with tempfile.TemporaryDirectory() as td:
        rc = Path(td) / ".bashrc"
        rc.write_text("export FOO=bar\nexport ANTHROPIC_BASE_URL=http://127.0.0.1:9000\n")
        os.environ["SLIMTOKEN_STATE_DIR"] = str(Path(td) / "state")
        cli.STATE_DIR = Path(td) / "state"
        cli.PREV_ENV = Path(td) / "state" / "prev_env"
        cli.main(["install", "--rc", str(rc)])
        text = rc.read_text()
        check("audit4 install keeps pre-existing BASE_URL",
              "ANTHROPIC_BASE_URL=http://127.0.0.1:9000" in text)
        check("audit4 install added marker", cli.MARKER_BEGIN in text)
        cli.main(["uninstall", "--rc", str(rc)])
        text = rc.read_text()
        check("audit4 uninstall removed marker", cli.MARKER_BEGIN not in text)
        n = text.count("ANTHROPIC_BASE_URL=")
        check("audit4 uninstall no duplicate BASE_URL", n == 1, f"count={n}")
        check("audit4 uninstall keeps original value",
              "ANTHROPIC_BASE_URL=http://127.0.0.1:9000" in text)



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


    moe16 = [r for r in rows if r["vram_gb"] == 16 and r["kind"] == "MoE"][0]
    check("16GB MoE capped at 128k", moe16["nominal_ctx"] == 131072,
          f"{moe16['nominal_ctx']}")
    check("16GB MoE is Qwen3.6-35B-A3B", moe16["model"] == "Qwen3.6-35B-A3B")


    only8 = cp.list_context_presets(8)
    check("vram filter 8 returns only 8GB", all(r["vram_gb"] == 8 for r in only8)
          and len(only8) >= 2)


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
             test_adapters, test_distill_user_preserved, test_json_tail_recall,
             test_source_tail_recall, test_openai_multimodal_roundtrip,
             test_output_filter_openai_delta, test_uninstall_no_duplicate,
             test_context_presets]
    print(f"slimtoken v{__version__} — running {len(tests)} test groups")
    for t in tests:
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n{'='*48}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())