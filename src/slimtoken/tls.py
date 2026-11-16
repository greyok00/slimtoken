"""Client TLS configuration from SLIMTOKEN_TLS_* environment variables."""

from __future__ import annotations

import os
import ssl
from typing import Optional

def make_client_tls_context(ca_path: Optional[str] = None,
                            cert_path: Optional[str] = None,
                            key_path: Optional[str] = None,
                            insecure: bool = False) -> ssl.SSLContext:
    """Build an SSLContext, optionally with a CA file, client cert chain, or no verification."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if ca_path:
        ctx.load_verify_locations(cafile=ca_path)
    if cert_path and key_path:
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx

def client_tls_from_env() -> Optional[ssl.SSLContext]:
    """Build a TLS context from SLIMTOKEN_TLS_* variables, or None if unset."""
    ca = os.environ.get("SLIMTOKEN_TLS_CA")
    cert = os.environ.get("SLIMTOKEN_TLS_CLIENT_CERT")
    key = os.environ.get("SLIMTOKEN_TLS_CLIENT_KEY")
    insecure = os.environ.get("SLIMTOKEN_TLS_INSECURE", "").lower() in (
        "1", "true", "yes", "on")
    if not (ca or cert or insecure):
        if insecure:
            return make_client_tls_context(insecure=True)
        return None
    return make_client_tls_context(ca, cert, key, insecure)

def wrap_client(sock, host: str, ctx: ssl.SSLContext):
    """Wrap a plain socket in TLS for the given host."""
    return ctx.wrap_socket(sock, server_hostname=host)
