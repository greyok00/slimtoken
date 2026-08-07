"""server — stdio MCP server exposing slimtoken's pipeline as tools.

Speaks the MCP JSON-RPC 2.0 protocol over stdio (the transport every MCP client
uses for local tools). The wire format mirrors the proven handshake in
:mod:`slimtoken.lazy_mcp` (initialize -> tools/list -> tools/call). stdio is
the default and only transport in this cut.

This server does NO optimization itself. It dispatches tool calls to
:mod:`slimtoken.mcp_server.tools`, which imports and calls the existing
pipeline / token-count / prune / compress / budget functions. The proxy and the
MCP server are independent processes that share the same core library.

Usage:
  slimtoken-mcp                       # stdio MCP server (for MCP clients)
  python -m slimtoken.mcp_server      # same, no install needed

The server is self-contained (stdlib only) so it runs wherever slimtoken is
installed, with no extra deps beyond slimtoken's existing ones.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

from . import tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "slimtoken-mcp"
SERVER_VERSION = "0.4.0"


def _send(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read() -> Optional[Dict[str, Any]]:
    line = sys.stdin.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception as e:
        _send({"jsonrpc": "2.0", "id": None,
               "error": {"code": -32700, "message": f"parse error: {e}"}})
        return None


def _result(id_: Any, result: Any) -> None:
    _send({"jsonrpc": "2.0", "id": id_, "result": result})


def _error(id_: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def _handle(req: Dict[str, Any]) -> bool:
    """Process one JSON-RPC request. Returns False to stop the loop."""
    method = req.get("method")
    id_ = req.get("id")
    params = req.get("params", {}) or {}

    if method == "initialize":
        _result(id_, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return True
    if method == "notifications/initialized":
        return True
    if method == "tools/list":
        _result(id_, {"tools": tools.tools_list()})
        return True
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        try:
            result = tools.handle(name, args)
            _result(id_, tools.to_content(result))
        except tools.ToolError as e:
            _result(id_, tools.to_error(str(e)))
        except Exception as e:
            _result(id_, tools.to_error(f"{type(e).__name__}: {e}"))
        return True
    # ping / unknown
    if method == "ping":
        _result(id_, {})
        return True
    if id_ is not None:
        _error(id_, -32601, f"method not found: {method}")
    return True


def run_stdio() -> int:
    """Main stdio loop. Reads newline-delimited JSON-RPC until EOF."""
    while True:
        req = _read()
        if req is None:
            break
        try:
            stop = _handle(req)
        except Exception as e:
            # never let one bad request kill the server
            _send({"jsonrpc": "2.0", "id": req.get("id"),
                   "error": {"code": -32603, "message": f"internal error: {e}"}})
            stop = True
        if stop is False:
            break
    return 0


def main(argv=None) -> int:
    return run_stdio()


if __name__ == "__main__":
    sys.exit(main())