"""slimtoken.mcp_server — MCP server exposing slimtoken's pipeline as tools.

Thin stdio adapter; calls existing slimtoken core functions (no reimplementation).
Entry point: ``slimtoken-mcp`` (see pyproject.toml).
"""
from .server import main, run_stdio  # noqa: F401