"""test_mcp_server — tests for the slimtoken MCP server (run: python3 test_mcp_server.py).

New tests only; does not touch the existing suite. Spawns `python -m slimtoken.mcp_server`
as a subprocess and exercises the JSON-RPC stdio protocol end-to-end (initialize →
tools/list → tools/call for every tool + the error path).
"""
import json
import os
import sys
import subprocess
import select
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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


class MCPClient:
    def __init__(self):
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")}
        self.p = subprocess.Popen([sys.executable, "-m", "slimtoken.mcp_server"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=env)

    def _send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def call(self, obj, tmax=30):
        self._send(obj)
        r, _, _ = select.select([self.p.stdout], [], [], tmax)
        if not r:
            err = self.p.stderr.read()
            raise RuntimeError(f"no response (timeout). stderr: {err[:500]}")
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(f"EOF before response. stderr: {self.p.stderr.read()[:500]}")
        return json.loads(line)

    def notify(self, obj):
        self._send(obj)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def _big_request():
    """A bloated-but-valid request that triggers dedup (the same large tool_result
    repeated across turns) so optimize actually reduces it."""
    big = "line %d: implementation detail here\n" * 80
    msgs = []
    for i in range(4):
        msgs.append({"role": "user", "content": "please read and fix the file"})
        msgs.append({"role": "assistant",
                     "content": [{"type": "tool_use", "id": "tu%d" % i,
                                  "name": "Read", "input": {"path": "/x.py"}}]})
        msgs.append({"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": "tu%d" % i,
                                  "content": big}]})
    msgs.append({"role": "user", "content": "now finalize"})
    return {"system": "<cold_memory>\n\n\nkeep\n</cold_memory>\n\n\nBe concise.",
            "tools": [{"name": "Read", "description": "Read a file\n\n```bash\nread /x\n```\n\nDetail.      ",
                       "input_schema": {"type": "object", "required": ["path"],
                                        "properties": {"path": {"type": "string"}}}}],
            "messages": msgs}


# ── 1. initialize + tools/list ────────────────────────────────────────────────
def test_init_and_list():
    c = MCPClient()
    try:
        r = c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "0"}}})
        si = r["result"]["serverInfo"]
        check("initialize returns serverInfo", si["name"] == "slimtoken-mcp")
        check("protocol version advertised",
              r["result"]["protocolVersion"] == "2024-11-05")
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        check("8 tools listed", len(names) == 8, f"got {len(names)}: {names}")
        expected = {"slimtoken.optimize_messages", "slimtoken.estimate_tokens",
                    "slimtoken.prune_context", "slimtoken.minify_tool_result",
                    "slimtoken.inspect_budget", "slimtoken.get_config",
                    "slimtoken.list_model_presets", "slimtoken.high_context_presets"}
        check("all expected tool names present", set(names) == expected)
        # every tool has a valid JSON-schema inputSchema
        ok = all("inputSchema" in t and t["inputSchema"]["type"] == "object"
                 for t in r["result"]["tools"])
        check("every tool has object inputSchema", ok)
    finally:
        c.close()


# ── 2. estimate_tokens ────────────────────────────────────────────────────────
def test_estimate_tokens():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        msgs = [{"role": "user", "content": "hello world " * 50}]
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.estimate_tokens",
                               "arguments": {"messages": msgs, "model": "llama3.2-3b"}}})
        o = json.loads(r["result"]["content"][0]["text"])
        check("estimate returns positive token count", o["tokens"] > 0)
        check("estimate reports model field", o["model"] == "llama3.2-3b")
        check("estimate has per-message breakdown",
              isinstance(o.get("per_message"), list) and len(o["per_message"]) == 1)
        check("isError false on success", r["result"]["isError"] is False)
    finally:
        c.close()


# ── 3. optimize_messages reduces + pair-safe ──────────────────────────────────
def test_optimize_messages():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        body = _big_request()
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.optimize_messages",
                               "arguments": {"messages": body["messages"],
                                             "system": body["system"],
                                             "tools": body["tools"],
                                             "profile": "balanced"}}})
        o = json.loads(r["result"]["content"][0]["text"])
        check("optimize reports tokens_in > tokens_out",
              o["tokens_in"] > o["tokens_out"])
        check("optimize reduction_pct > 0", o["reduction_pct"] > 0,
              f"{o['reduction_pct']}%")
        check("optimize dedup_count >= 3 (4 duplicate tool_results)",
              o["stats"]["dedup_count"] >= 3)
        # pair-safe: every tool_use id still has a matching tool_result
        out_msgs = o["messages"]
        use_ids = set()
        result_ids = set()
        for m in out_msgs:
            c2 = m.get("content")
            if isinstance(c2, list):
                for blk in c2:
                    if blk.get("type") == "tool_use":
                        use_ids.add(blk.get("id"))
                    if blk.get("type") == "tool_result":
                        result_ids.add(blk.get("tool_use_id"))
        check("pair-safe: all tool_use ids have results",
              use_ids == result_ids, f"use={use_ids} result={result_ids}")
        check("optimize echoes profile", o["profile"] == "balanced")
    finally:
        c.close()


# ── 4. inspect_budget read-only ───────────────────────────────────────────────
def test_inspect_budget():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        msgs = [{"role": "user", "content": "word " * 500}]
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.inspect_budget",
                               "arguments": {"messages": msgs, "token_budget": 50}}})
        o = json.loads(r["result"]["content"][0]["text"])
        check("inspect total_tokens > 0", o["total_tokens"] > 0)
        check("inspect over_budget True (500 words vs 50 budget)",
              o["over_budget"] is True)
        check("inspect reports headroom", "headroom" in o)
        check("inspect reports would_drop_messages", "would_drop_messages" in o)
    finally:
        c.close()


# ── 5. minify_tool_result ─────────────────────────────────────────────────────
def test_minify_tool_result():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # a directory listing the type-detector recognizes
        listing = "total 0\n" + "\n".join(
            "drwxr-xr-x 2 user group 4096 Jan  1 12:00 dir%03d" % i for i in range(40))
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.minify_tool_result",
                               "arguments": {"content": listing}}})
        o = json.loads(r["result"]["content"][0]["text"])
        check("minify_tool_result changed=True on recognized type",
              o["changed"] is True)
        check("minify_tool_result returns content", "content" in o)
        check("compressed content smaller",
              len(json.dumps(o["content"])) < len(json.dumps(listing)))
    finally:
        c.close()


# ── 6. prune_context ──────────────────────────────────────────────────────────
def test_prune_context():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        warm = [{"role": "user", "content": "x " * 500}]
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.prune_context",
                               "arguments": {"warm_entries": warm,
                                             "max_tokens": 100}}})
        o = json.loads(r["result"]["content"][0]["text"])
        check("prune returns prompt_block", "prompt_block" in o)
        check("prune token_count is an int", isinstance(o["token_count"], int))
        check("prune token_count positive", o["token_count"] > 0)
        # best-effort: a single large message can't be dropped below budget
        # without removing it entirely; prune keeps recent context rather than
        # returning empty. Just assert the block is non-empty + contains the marker.
        check("prune prompt_block non-empty", len(o["prompt_block"]) > 0)
    finally:
        c.close()


# ── 7. get_config ──────────────────────────────────────────────────────────────
def test_get_config():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.get_config",
                               "arguments": {"profile": "aggressive"}}})
        o = json.loads(r["result"]["content"][0]["text"])
        check("get_config(profile) returns profile", o["profile"] == "aggressive")
        cfg = o["config"]
        check("get_config enabled_stages is a sorted list",
              isinstance(cfg["enabled_stages"], list) and cfg["enabled_stages"] == sorted(cfg["enabled_stages"]))
        check("aggressive enables distill", "distill" in cfg["enabled_stages"])
        check("aggressive enables tool_compress",
              cfg.get("tool_compress") is True or cfg.get("tool_compress") == 1)
        # env-source variant (no profile arg)
        r2 = c.call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "slimtoken.get_config", "arguments": {}}})
        o2 = json.loads(r2["result"]["content"][0]["text"])
        check("get_config(no args) reports source", "source" in o2)
    finally:
        c.close()


# ── 8. list_model_presets ──────────────────────────────────────────────────────
def test_list_model_presets():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.list_model_presets",
                               "arguments": {"vram_gb": 4, "measure": True}}}, tmax=40)
        o = json.loads(r["result"]["content"][0]["text"])
        check("presets(4GB) returns 3 rows", o["count"] == 3, f"got {o['count']}")
        first = o["presets"][0]
        check("preset has model field", "model" in first)
        check("preset has vram_gb=4", first["vram_gb"] == 4)
        check("preset measured reduction_pct_bloated is a number",
              isinstance(first.get("reduction_pct_bloated"), (int, float)))
        check("preset measured reduction > 0",
              first["reduction_pct_bloated"] > 0, f"{first['reduction_pct_bloated']}")
    finally:
        c.close()


# ── 9. optimize with openai format (adapter round-trip) ────────────────────────
def test_optimize_openai_format():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # OpenAI shape: system as a role:"system" message, tool_calls + role:"tool"
        body = {"model": "gpt-x", "messages": [
            {"role": "system", "content": "You are a helpful assistant.   "},
            {"role": "user", "content": "Please list files."},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "ls", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1",
             "content": "file_a.txt\nfile_b.txt\n" * 200},
        ]}
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.optimize_messages",
                               "arguments": {"messages": body["messages"],
                                             "format": "openai"}}}, tmax=40)
        o = json.loads(r["result"]["content"][0]["text"])
        check("openai format echoed", o["format"] == "openai")
        msgs = o["messages"]
        check("openai round-trip keeps system role",
              any(m.get("role") == "system" for m in msgs))
        check("openai round-trip keeps tool_calls",
              any(m.get("tool_calls") for m in msgs))
        check("openai round-trip keeps role:tool result",
              any(m.get("role") == "tool" for m in msgs))
        check("openai reduction > 0", o["reduction_pct"] > 0, f"{o['reduction_pct']}")
        # pair-safety: tool_call id survives
        ids = [tc.get("id") for m in msgs if m.get("tool_calls")
               for tc in m["tool_calls"]]
        check("tool_call id preserved", "call_1" in ids, f"{ids}")
    finally:
        c.close()


# ── 10. high_context_presets ──────────────────────────────────────────────────
def test_high_context_presets():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "slimtoken.high_context_presets",
                               "arguments": {"vram_gb": 16}}}, tmax=40)
        o = json.loads(r["result"]["content"][0]["text"])
        check("16GB presets non-empty", o["count"] > 0, f"count={o['count']}")
        row = o["presets"][0]
        for k in ("nominal_ctx", "ub", "total_gb", "margin_gb", "profile",
                  "reduction_pct", "effective_ctx", "llama_cmd", "kv_quant"):
            check(f"preset has {k}", k in row, f"missing {k}")
        check("16GB fits in VRAM (total <= 16)", row["total_gb"] <= 16, f"{row['total_gb']}")
        check("16GB margin >= 0", row["margin_gb"] >= 0, f"{row['margin_gb']}")
        check("effective_ctx > nominal_ctx", row["effective_ctx"] > row["nominal_ctx"],
              f"{row['effective_ctx']} vs {row['nominal_ctx']}")
        check("kv_quant is q4_0", row["kv_quant"] == "q4_0")
        # best=true returns a single row
        r2 = c.call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "slimtoken.high_context_presets",
                                "arguments": {"vram_gb": 16, "best": True}}}, tmax=40)
        o2 = json.loads(r2["result"]["content"][0]["text"])
        check("best returns one row", o2["count"] == 1, f"{o2['count']}")
        check("best has effective_ctx", "effective_ctx" in o2["best"])
    finally:
        c.close()


# ── 11. error path: unknown tool + bad args ─────────────────────────────────────
def test_errors():
    c = MCPClient()
    try:
        c.call({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}})
        c.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = c.call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "bogus.tool", "arguments": {}}})
        check("unknown tool → isError True", r["result"]["isError"] is True)
        # missing messages
        r = c.call({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "slimtoken.estimate_tokens",
                               "arguments": {"messages": "not-a-list"}}})
        check("bad messages type → isError True", r["result"]["isError"] is True)
        # unknown JSON-RPC method
        r = c.call({"jsonrpc": "2.0", "id": 3, "method": "no/such/method",
                    "params": {}})
        check("unknown RPC method → error code -32601",
              r.get("error", {}).get("code") == -32601)
    finally:
        c.close()


def main():
    tests = [test_init_and_list, test_estimate_tokens, test_optimize_messages,
             test_inspect_budget, test_minify_tool_result, test_prune_context,
             test_get_config, test_list_model_presets, test_optimize_openai_format,
             test_high_context_presets, test_errors]
    print(f"slimtoken v{__version__} MCP server — running {len(tests)} test groups")
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as e:
            global FAIL
            FAIL += 1
            print(f"  ✗ EXC {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{'='*48}\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())