"""test_all — pytest-free tests for slimtoken (run: python3 test_all.py).

Tests run AGAINST THE COMPILED .so (build first: `python3 setup.py build_ext --inplace`).
If the .so is absent they fall back to the pure-Python .py — but the product ships
compiled, so build before testing.
"""
import json
import os
import re
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


# ── 11. proxy e2e (local, against compiled .so) ──────────────────────────────
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
        check("proxy started (compiled .so)", ok)
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


def main():
    tests = [test_fences, test_tools, test_system_and_budget, test_config_optimizer,
             test_install_uninstall, test_dedup, test_distill,
             test_pair_safety_defaults, test_default_reduction, test_lazy_mcp_smoke,
             test_proxy_e2e]
    print(f"slimtoken v{__version__} — running {len(tests)} test groups")
    for t in tests:
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n{'='*48}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())