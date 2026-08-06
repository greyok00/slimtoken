#!/usr/bin/env python3
"""benchmark — measure slimtoken's real effect (pure Python, or compiled .so if built).

Build first: `python3 setup.py build_ext --inplace`.

Reports, with no fabrication:
  1. PAYLOAD REDUCTION  — tokens in vs out, per payload size, with DEFAULT config
                          (tools+system+messages+dedup+distill all ON)
  2. PER-STAGE BREAKDOWN
  3. PROXY OVERHEAD    — ms the minify pipeline adds (median of N runs, vs .so)
  4. END-TO-END        — if a llama-server backend is reachable, sends the SAME
                          request raw vs minified and reports the model's own
                          usage.input_tokens + wall-clock.

Run:
  python3 bench/benchmark.py                       # payload + overhead (no backend)
  python3 bench/benchmark.py --backend http://127.0.0.1:8082   # + end-to-end
  python3 bench/benchmark.py --json                # machine-readable
"""
from __future__ import annotations
import argparse, copy, json, os, statistics, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from slimtoken.pipeline import minify_request, MinifyConfig

# ── token estimate (OpenAI-ish: ~4 chars/token) ──────────────────────────────
def tok(obj) -> int:
    return max(1, len(json.dumps(obj, separators=(",", ":"))) // 4)

# ── synthetic but realistic payloads ─────────────────────────────────────────
VERBOSE_SYS = (
    "<cold_memory>\nYou are a senior engineer. Follow project conventions strictly.\n"
    "Never leak personal info. Use Path.home() for paths.\n</cold_memory>\n\n\n\n"
    "You are an interactive CLI coding agent.\n\n"
    "IMPORTANT: Assist with authorized security testing. Refuse destructive requests.\n\n"
    "When you use a tool, explain what was done in plain language. Never dump raw tool names.\n\n\n"
    "```python\ndef example(x):\n    return x + 1\n```\n\n\n"
    "Be concise. Use tables when comparing. End substantive work with a STATE block."
)

def make_tool(idx: int, examples: int) -> dict:
    desc = ("Use this tool to perform operation %d on the local filesystem and return "
            "its full result. You can address resources by their absolute path. "
            "The tool returns the result as a string. Here is an example of how to call it:\n\n" % idx)
    for e in range(examples):
        desc += "```bash\ntool_%d /tmp/example_%d.txt\n```\n\nAnother example:\n\n" % (idx, e)
    desc += "More detail about edge cases and error handling follows here for completeness."
    return {
        "name": "Tool_%d" % idx,
        "description": desc,
        "input_schema": {
            "type": "object", "title": "Tool%dSchema" % idx, "$comment": "internal",
            "required": ["path", "mode"],
            "properties": {
                "path": {"type": "string", "examples": ["/a", "/b", "/c"], "description": "abs path"},
                "mode": {"type": "string", "enum": ["r", "w", "rw"], "description": "access mode"},
            },
        },
    }

def make_payload(size: str) -> dict:
    cfg = {"small": (3, 1, 4), "medium": (6, 3, 12), "large": (10, 4, 24), "bloated": (6, 4, 26)}
    n_tools, n_examples, n_turns = cfg[size]
    msgs = []
    if size == "bloated":
        # realistic bloat: the SAME big file re-read every turn (dedup target)
        # + a long verbose assistant explanation every turn (distill target)
        big_file = "".join("line %d: implementation detail here\n" % i for i in range(400))
        long_explain = ("Let me walk through my reasoning in detail. I considered several "
                        "approaches and chose this one due to the constraints involved. " * 25)
        for i in range(8):
            msgs.append({"role": "user", "content": "please read and fix the file"})
            msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "tu%d" % i,
                                                           "name": "Tool_%d" % (i % n_tools),
                                                           "input": {"path": "/x.py", "mode": "r"}}]})
            msgs.append({"role": "user", "content": [{"type": "tool_result",
                                                      "tool_use_id": "tu%d" % i, "content": big_file}]})
            msgs.append({"role": "assistant", "content": long_explain})
        msgs.append({"role": "user", "content": "now finalize"})
        return {"system": VERBOSE_SYS, "tools": [make_tool(i, n_examples) for i in range(n_tools)],
                "messages": msgs}
    for i in range(n_turns):
        msgs.append({"role": "user", "content": "question %d\n\n\n\nmore detail about task %d here" % (i, i)})
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": "tu%d" % i,
                                                       "name": "Tool_%d" % (i % n_tools),
                                                       "input": {"path": "/tmp/f%d" % i, "mode": "r"}}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu%d" % i,
                                                  "content": "result %d\n\n\n\nlots\n\n\nof\n\n\nblank\n\n\nlines\n\n\nhere" % i}]})
        msgs.append({"role": "assistant", "content": "answer %d\n\n\n\nexplanation of the answer" % i})
    msgs.append({"role": "user", "content": "now do the final task\n\n\n\nplease proceed"})
    return {"system": VERBOSE_SYS, "tools": [make_tool(i, n_examples) for i in range(n_tools)], "messages": msgs}

# ── 1. payload reduction (DEFAULT config = all stages ON) ────────────────────
def bench_payload():
    rows = []
    for size in ("small", "medium", "large", "bloated"):
        body = make_payload(size)
        tin = tok(body)
        out, _ = minify_request(copy.deepcopy(body), MinifyConfig())  # all defaults
        tdef = tok(out)
        # also a no-distill/no-dedup baseline (tools+system+messages only) for contrast
        base, _ = minify_request(copy.deepcopy(body),
                                 MinifyConfig(enabled_stages={"tools", "system", "messages"},
                                              token_budget=0))
        tbase = tok(base)
        rows.append({"size": size, "in": tin, "base": tbase, "default": tdef,
                     "def_pct": 100 * (tin - tdef) / tin,
                     "base_pct": 100 * (tin - tbase) / tin})
    return rows

# ── 2. per-stage breakdown (medium payload) ──────────────────────────────────
def bench_stages():
    body = make_payload("medium")
    tin = tok(body)
    out = []
    for stage in ("tools", "system", "messages", "dedup", "distill"):
        cfg = MinifyConfig(enabled_stages={stage}, token_budget=0)
        o, _ = minify_request(copy.deepcopy(body), cfg)
        out.append({"stage": stage, "tokens": tok(o), "saved": tin - tok(o),
                    "pct": 100 * (tin - tok(o)) / tin})
    o, _ = minify_request(copy.deepcopy(body), MinifyConfig())
    out.append({"stage": "all(default)", "tokens": tok(o), "saved": tin - tok(o),
                "pct": 100 * (tin - tok(o)) / tin})
    out.insert(0, {"stage": "(original)", "tokens": tin, "saved": 0, "pct": 0.0})
    return out

# ── 3. proxy overhead (minify pipeline only, large payload, vs .so) ─────────
def bench_overhead(runs=50):
    body = make_payload("large")
    cfg = MinifyConfig()
    minify_request(copy.deepcopy(body), cfg)  # warm
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        minify_request(copy.deepcopy(body), cfg)
        times.append((time.perf_counter() - t0) * 1000)
    return {"runs": runs, "median_ms": round(statistics.median(times), 3),
            "p95_ms": round(sorted(times)[int(len(times) * 0.95) - 1], 3),
            "mean_ms": round(statistics.mean(times), 3)}

# ── 4. end-to-end through the REAL proxy (needs a live llama-server) ─────────
def _post(url, body_bytes, timeout=60):
    req = urllib.request.Request(url, data=body_bytes, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(); status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read(); status = e.code
    except Exception as e:
        return None, 0.0, str(e), 0
    return raw, time.perf_counter() - t0, None, status

def _usage(raw):
    if not raw:
        return {}
    try:
        j = json.loads(raw)
    except Exception:
        return {}
    if j.get("error"):
        return {"error": j["error"] if isinstance(j["error"], str) else json.dumps(j["error"])}
    u = j.get("usage", {})
    inp = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    if not inp:
        inp = u.get("prompt_tokens") or 0
    return {"input_tokens": inp,
            "output_tokens": u.get("output_tokens") or u.get("completion_tokens") or 0}

def _wait_port(port, tries=40):
    import socket
    for _ in range(tries):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return True
        except OSError:
            time.sleep(0.15)
    return False

def bench_e2e(backend: str, n: int = 5):
    """Send the SAME Anthropic payload (with tools) two ways:
       (a) RAW       — direct to backend /v1/messages
       (b) OPTIMIZED — through the proxy, which minifies then forwards
       Compare the model's own reported input_tokens + wall-clock.
       A unique nonce per request defeats llama-server's prefix cache."""
    import subprocess
    backend = backend.rstrip("/")
    proxy_port = 8287
    env = dict(os.environ)
    env["SLIMTOKEN_PORT"] = str(proxy_port)
    env["SLIMTOKEN_UPSTREAM"] = backend
    env["SLIMTOKEN_MINIFY_BUDGET"] = "0"  # keep full payload; measure minify not pruning
    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONPATH"] = src_dir + ":" + env.get("PYTHONPATH", "")
    # spawn via -c so the compiled .so is used (runpy can't run extension modules)
    proc = subprocess.Popen([sys.executable, "-u", "-c",
                             "from slimtoken.proxy import main; main()"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_port(proxy_port):
            return {"error": "proxy did not start"}
        _post(backend + "/v1/messages", json.dumps(
            {"model": "x", "messages": [{"role": "user", "content": "warmup"}],
             "max_tokens": 1}).encode())
        ctx_cap = 2048
        try:
            r = urllib.request.urlopen(backend.rstrip("/") + "/props", timeout=3)
            p = json.loads(r.read())
            ctx_cap = p.get("default_generation_settings", {}).get("n_ctx") or p.get("n_ctx") or ctx_cap
        except Exception:
            pass
        size = "large" if ctx_cap >= 8192 else ("medium" if ctx_cap >= 4096 else "small")
        base = make_payload(size)
        nonce = [0]
        def one(body, url):
            b = copy.deepcopy(body)
            nonce[0] += 1
            b["messages"][-1]["content"] = b["messages"][-1]["content"] + " nonce-%d" % nonce[0]
            b["model"] = "bench"
            b["max_tokens"] = 8
            r, wall, err, status = _post(url, json.dumps(b).encode())
            if err:
                return {"error": err, "wall_s": round(wall, 3)}
            u = _usage(r)
            u["wall_s"] = round(wall, 3)
            if u.get("input_tokens", 0) == 0 and not u.get("error"):
                u["error"] = "no input_tokens (status %s, ctx cap %d, size %s)" % (status, ctx_cap, size)
            return u
        raw_runs = [one(base, backend + "/v1/messages") for _ in range(n)]
        opt_runs = [one(base, "http://127.0.0.1:%d/v1/messages" % proxy_port) for _ in range(n)]
        def med(xs, k):
            vals = [x.get(k) for x in xs if x and k in x and not x.get("error")]
            return round(statistics.median(vals), 3) if vals else None
        if any(r.get("error") for r in raw_runs) or any(o.get("error") for o in opt_runs):
            return {"error": (raw_runs[0].get("error") or opt_runs[0].get("error")),
                    "raw_runs": raw_runs, "opt_runs": opt_runs}
        raw_in = med(raw_runs, "input_tokens"); opt_in = med(opt_runs, "input_tokens")
        raw_wall = med(raw_runs, "wall_s"); opt_wall = med(opt_runs, "wall_s")
        return {
            "backend": backend, "runs": n, "size": size,
            "raw_input_tokens": raw_in, "opt_input_tokens": opt_in,
            "tokens_saved": (raw_in - opt_in) if (raw_in and opt_in) else None,
            "token_reduction_pct": round(100 * (raw_in - opt_in) / raw_in, 1) if (raw_in and opt_in) else None,
            "raw_wall_s": raw_wall, "opt_wall_s": opt_wall,
            "raw_runs": raw_runs, "opt_runs": opt_runs,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

# ── reporting ────────────────────────────────────────────────────────────────
def fmt_table(headers, rows):
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    sep = "+".join("-" * (w[i] + 2) for i in range(len(headers)))
    out = ["|" + "|".join(" %-*s " % (w[i], h) for i, h in enumerate(headers)) + "|", sep]
    for r in rows:
        out.append("|" + "|".join(" %-*s " % (w[i], r[i]) for i in range(len(headers))) + "|")
    return "\n".join(out)

def report(payload, stages, overhead, e2e):
    print("=" * 66)
    print(" slimtoken — benchmark")
    print("=" * 66)
    print("\n[1] PAYLOAD REDUCTION  (est. tokens, ~4 chars/token; DEFAULT = all stages ON)")
    print("-" * 66)
    rows = [[r["size"], r["in"], r["base"], "%.1f%%" % r["base_pct"],
             r["default"], "%.1f%%" % r["def_pct"]]
            for r in payload]
    print(fmt_table(["size", "raw tok", "base(3stg)", "saved", "default", "saved"], rows))
    print("\n  base = tools+system+messages only   ·   default = +dedup +distill (prompt pruning)")
    print("\n[2] PER-STAGE BREAKDOWN  (medium payload)")
    print("-" * 66)
    rows = [[s["stage"], s["tokens"], s["saved"], "%.1f%%" % s["pct"]] for s in stages]
    print(fmt_table(["config", "tokens", "saved", "reduction"], rows))
    print("\n[3] PROXY OVERHEAD  (minify pipeline only, large payload)")
    print("-" * 66)
    print("  median %s ms  ·  p95 %s ms  ·  mean %s ms   (n=%d)" % (
        overhead["median_ms"], overhead["p95_ms"], overhead["mean_ms"], overhead["runs"]))
    if e2e and not e2e.get("error") and e2e.get("token_reduction_pct") is not None:
        print("\n[4] END-TO-END through the real proxy  (backend: %s, n=%d, payload=%s)" % (
            e2e["backend"], e2e["runs"], e2e.get("size", "?")))
        print("    same Anthropic payload (with tools) sent RAW-direct vs OPTIMIZED-through-proxy")
        print("-" * 66)
        rows = [
            ["input_tokens (median)", e2e["raw_input_tokens"], e2e["opt_input_tokens"],
             "%d" % e2e["tokens_saved"], "%.1f%%" % e2e["token_reduction_pct"]],
            ["wall-clock s (median)", e2e["raw_wall_s"], e2e["opt_wall_s"], "-", "-"],
        ]
        print(fmt_table(["metric", "raw", "optimized", "saved", "reduction"], rows))
        print("\n    -> The model reports %.0f -> %.0f input tokens (%.1f%% fewer actually processed)."
              % (e2e["raw_input_tokens"], e2e["opt_input_tokens"], e2e["token_reduction_pct"]))
    elif e2e:
        print("\n[4] END-TO-END: skipped (%s)" % e2e.get("error", "backend unreachable"))
    else:
        print("\n[4] END-TO-END: skipped (pass --backend URL to enable)")
    print("\n" + "=" * 66)

def main():
    ap = argparse.ArgumentParser(description="benchmark slimtoken (compiled)")
    ap.add_argument("--backend", default=os.environ.get("SLIMTOKEN_BENCH_BACKEND", ""),
                    help="llama-server URL for end-to-end (e.g. http://127.0.0.1:8082)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of tables")
    ap.add_argument("--e2e-runs", type=int, default=5)
    a = ap.parse_args()
    payload = bench_payload()
    stages = bench_stages()
    overhead = bench_overhead()
    e2e = bench_e2e(a.backend, a.e2e_runs) if a.backend else None
    res = {"payload": payload, "stages": stages, "overhead": overhead, "e2e": e2e}
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        report(payload, stages, overhead, e2e)

if __name__ == "__main__":
    main()