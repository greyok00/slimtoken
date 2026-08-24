#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def _have_cli() -> bool:
    return shutil.which("slimtoken") is not None


def _run_cli(argv: list[str]) -> int:
    cmd = ["slimtoken"] + argv
    return subprocess.call(cmd)


def _mcp_available() -> bool:

    if shutil.which("slimtoken-mcp"):
        return True
    try:
        subprocess.run([sys.executable, "-c",
                        "import slimtoken.mcp_server  # noqa"],
                       capture_output=True, check=True,
                       env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")})
        return True
    except Exception:
        return False


def _mcp_call(tool: str, arguments: dict) -> dict:

    import select
    bin = shutil.which("slimtoken-mcp") or sys.executable
    base = [bin] if shutil.which("slimtoken-mcp") else [sys.executable, "-m", "slimtoken.mcp_server"]
    p = subprocess.Popen(base, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()

    def read(tmax=15):
        r, _, _ = select.select([p.stdout], [], [], tmax)
        if not r:
            raise RuntimeError("MCP server did not respond (timeout)")
        return json.loads(p.stdout.readline())

    send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "skill", "version": "0"}}})
    read()
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
          "params": {"name": tool, "arguments": arguments}})
    resp = read(tmax=40)
    p.stdin.close()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    if resp.get("result", {}).get("isError"):
        raise RuntimeError(resp["result"]["content"][0]["text"])
    return json.loads(resp["result"]["content"][0]["text"])


def _read_body(args) -> dict:
    src = args.input if args.input and args.input != "-" else sys.stdin
    raw = src.read() if hasattr(src, "read") else open(args.input).read()
    return json.loads(raw)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="optimize.py",
                                 description="slimtoken skill wrapper (CLI primary, MCP fallback)")
    sub = ap.add_subparsers(dest="cmd")

    o = sub.add_parser("optimize")
    o.add_argument("--input", "-i", default=None)
    o.add_argument("--max-input-tokens", type=int, default=None)
    o.add_argument("--json", action="store_true")

    pr = sub.add_parser("presets")
    pr.add_argument("--vram-gb", type=int, default=None)
    pr.add_argument("--measure", action="store_true")

    est = sub.add_parser("estimate")
    est.add_argument("--input", "-i", default=None)
    est.add_argument("--model", default=None)

    args = ap.parse_args(argv)
    cmd = args.cmd or "optimize"
    if cmd == "optimize" and not (args.input):

        pass


    if _have_cli():
        if cmd == "optimize":
            cli_args = ["optimize"]
            if args.input:  cli_args += ["-i", args.input]
            if args.max_input_tokens is not None:
                cli_args += ["--max-input-tokens", str(args.max_input_tokens)]
            if args.json:   cli_args += ["--json"]
            return _run_cli(cli_args)
        if cmd == "presets":
            cli_args = ["presets"]
            if args.vram_gb: cli_args += ["--vram-gb", str(args.vram_gb)]
            if args.measure:  cli_args += ["--measure"]
            return _run_cli(cli_args)
        if cmd == "estimate":


            cli_args = ["optimize"]
            if args.input: cli_args += ["-i", args.input]
            return _run_cli(cli_args)


    if not _mcp_available():
        print("slimtoken not found: install the CLI (`pip install slimtoken`) "
              "or run `slimtoken-mcp`.", file=sys.stderr)
        return 1

    if cmd == "optimize":
        body = _read_body(args)
        a = {"messages": body.get("messages", [])}
        if "system" in body: a["system"] = body["system"]
        if "tools" in body:  a["tools"] = body["tools"]
        if args.max_input_tokens is not None:
            a["max_input_tokens"] = args.max_input_tokens
        r = _mcp_call("slimtoken.optimize_messages", a)
        out = {"messages": r["messages"]}
        if r.get("system") is not None: out["system"] = r["system"]
        if r.get("tools") is not None:  out["tools"] = r["tools"]
        print(json.dumps(out, ensure_ascii=False, default=str))
        print(f"tokens: {r['tokens_in']} -> {r['tokens_out']}  "
              f"(-{r['reduction_pct']}%)", file=sys.stderr)
        return 0
    if cmd == "presets":
        a = {"measure": args.measure}
        if args.vram_gb: a["vram_gb"] = args.vram_gb
        r = _mcp_call("slimtoken.list_model_presets", a)
        for row in r["presets"]:
            red = row.get("reduction_pct_bloated")
            reds = f"{red:>5}%" if red is not None else "  n/a"
            print(f"{row['vram_gb']:>4}GB {row['model'][:38]:38} "
                  f"{row['quant'][:8]:8} {row['context']:>7} {reds}")
        return 0
    if cmd == "estimate":
        body = _read_body(args)
        a = {"messages": body.get("messages", [])}
        if "system" in body: a["system"] = body["system"]
        if "tools" in body:  a["tools"] = body["tools"]
        if args.model: a["model"] = args.model
        r = _mcp_call("slimtoken.estimate_tokens", a)
        print(f"tokens: {r['tokens']}  (system={r['system_tokens']} "
              f"tools={r['tools_tokens']} messages={r['messages_tokens']})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())