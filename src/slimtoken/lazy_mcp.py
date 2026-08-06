#!/usr/bin/env python3
"""lazy_mcp — generic thin wrapper for optional MCP servers (slimtoken).

Exposes ONE stub tool per configured MCP server. When the stub is called, it
spawns the real MCP server (stdio), performs the JSON-RPC handshake, lists
the real tools, proxies the requested call, and shuts the real server down.
This keeps idle tool tax minimal: instead of N verbose tool schemas loaded on
every request, the model sees a single small stub until it actually needs the
server.

Config (JSON file, default ``~/.slimtoken/lazy_mcp.json``):
  Each entry: {"name": "firecrawl",
               "command": ["npx", "-y", "firecrawl-mcp"],
               "tools_hint": ["scrape", "search", ...]}
  Missing/empty config = no servers = the stub reports "not configured" (no-op).

Usage:
  slimtoken lazy-mcp --name firecrawl      # run the stdio MCP stub server
  slimtoken lazy-mcp smoke                 # self-test (no network)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def config_path() -> Path:
    return Path(os.environ.get(
        "SLIMTOKEN_LAZY_MCP_CONFIG",
        str(Path.home() / ".slimtoken" / "lazy_mcp.json"))).expanduser()


def load_config(name: Optional[str] = None) -> List[Dict[str, Any]]:
    p = config_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        entries = data if isinstance(data, list) else data.get("servers", [])
        if name:
            return [e for e in entries if e.get("name") == name]
        return entries
    except Exception as e:
        print(f"lazy_mcp: config error: {e}", file=sys.stderr)
        return []


def _send_json(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_json(stream) -> Optional[Dict[str, Any]]:
    line = stream.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def _proxy_server(command: List[str]) -> Tuple[subprocess.Popen, int]:
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)

    # handshake (some servers send initialize first)
    hello = _read_json(proc.stdout)
    if hello and hello.get("method") == "initialize":
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": hello.get("id"),
            "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "slimtoken-lazy-mcp", "version": "1.0"},
            }
        }) + "\n")
        proc.stdin.flush()

    proc.stdin.write(json.dumps({
        "jsonrpc": "2.0", "method": "initialize", "id": 1,
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "slimtoken-lazy-mcp", "version": "1.0"}}
    }) + "\n")
    proc.stdin.flush()
    resp = _read_json(proc.stdout)
    if not resp or "result" not in resp:
        err = proc.stderr.read(400) if proc.stderr else ""
        raise RuntimeError(f"real server init failed: {resp or err}")

    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 2}) + "\n")
    proc.stdin.flush()
    tools_resp = _read_json(proc.stdout)
    real_tools = (tools_resp or {}).get("result", {}).get("tools", [])
    return proc, len(real_tools)


def _call_real(proc: subprocess.Popen, name: str, arguments: Dict[str, Any], req_id: Any) -> Dict[str, Any]:
    proc.stdin.write(json.dumps({
        "jsonrpc": "2.0", "method": "tools/call", "id": 3,
        "params": {"name": name, "arguments": arguments}
    }) + "\n")
    proc.stdin.flush()
    resp = _read_json(proc.stdout)
    if not resp:
        err = proc.stderr.read(400) if proc.stderr else ""
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32000, "message": f"real server no response: {err}"}}
    if "error" in resp:
        return {"jsonrpc": "2.0", "id": req_id, "error": resp["error"]}
    return {"jsonrpc": "2.0", "id": req_id, "result": resp.get("result", {})}


def _shutdown(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except Exception:
        pass


def _build_stub(entry: Dict[str, Any]) -> Dict[str, Any]:
    name = entry["name"]
    hint = entry.get("tools_hint", [])
    hint_str = f" Expands to: {', '.join(hint)}." if hint else ""
    return {
        "name": f"lazy_{name}",
        "description": f"Lazy proxy for optional MCP server '{name}'. Spawns the "
                       f"real server on call.{hint_str} Arguments: "
                       f'{{"real_tool": string, "arguments": object}}.',
        "inputSchema": {
            "type": "object",
            "properties": {
                "real_tool": {"type": "string", "description": "Tool name on the real server."},
                "arguments": {"type": "object", "description": "Arguments for real_tool."},
            },
            "required": ["real_tool"],
        },
    }


def run_server(name: str) -> None:
    """Run the stdio MCP stub server for one configured entry."""
    entries = load_config(name)
    if not entries:
        stub = {
            "name": f"lazy_{name}",
            "description": f"Optional MCP server '{name}' is not configured.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        }
    else:
        stub = _build_stub(entries[0])

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except Exception as e:
            _send_json({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": str(e)}})
            continue

        method = req.get("method")
        _id = req.get("id")
        params = req.get("params", {})

        if method == "initialize":
            _send_json({"jsonrpc": "2.0", "id": _id, "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": f"lazy-{name}", "version": "1.0"},
            }})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _send_json({"jsonrpc": "2.0", "id": _id, "result": {"tools": [stub]}})
        elif method == "tools/call":
            tname = params.get("name", "")
            if tname != stub["name"]:
                _send_json({"jsonrpc": "2.0", "id": _id,
                            "error": {"code": -32601, "message": f"Unknown tool: {tname}"}})
                continue
            if not entries:
                _send_json({"jsonrpc": "2.0", "id": _id, "error": {
                    "code": -32000,
                    "message": f"'{name}' not configured in {config_path()}"}})
                continue
            args = params.get("arguments", {})
            real_tool = args.get("real_tool", "")
            real_args = args.get("arguments", {})
            command = entries[0].get("command", [])
            if isinstance(command, str):
                command = command.split()
            proc = None
            try:
                proc, _ = _proxy_server(command)
                _send_json(_call_real(proc, real_tool, real_args, _id))
            except Exception as e:
                _send_json({"jsonrpc": "2.0", "id": _id,
                            "error": {"code": -32000, "message": f"lazy proxy failed: {e}"}})
            finally:
                if proc is not None:
                    _shutdown(proc)
        else:
            _send_json({"jsonrpc": "2.0", "id": _id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"}})


def smoke() -> int:
    entries = load_config()
    print(f"config: {config_path()}")
    print(f"config entries: {len(entries)}")
    if entries:
        stub = _build_stub(entries[0])
        print(f"stub tool: {stub['name']}")
    print("lazy_mcp: OK")
    return 0


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="slimtoken lazy-mcp",
                                 description="lazy MCP stub server")
    ap.add_argument("--name", default=os.environ.get("SLIMTOKEN_LAZY_MCP_NAME", ""))
    ap.add_argument("smoke", nargs="?", default=None)
    args = ap.parse_args(argv)

    if args.smoke == "smoke":
        sys.exit(smoke())
    if not args.name:
        print("usage: slimtoken lazy-mcp --name SERVER_NAME  (or: slimtoken lazy-mcp smoke)",
              file=sys.stderr)
        sys.exit(2)
    run_server(args.name)


if __name__ == "__main__":
    main()