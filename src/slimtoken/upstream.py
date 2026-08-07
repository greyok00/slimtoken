"""upstream — where the proxy forwards minified requests.

Two flavors:
  - local:  plain HTTP to a llama-server on 127.0.0.1 (no TLS)
  - cloud:  HTTPS to a cloud endpoint (TLS via tls.py)

For cloud we use stdlib http.client (HTTPSConnection), which handles TLS +
HTTP/1.1 cleanly. For local we keep the raw-socket streaming path (lowest
latency for SSE). The choice is made from the upstream URL scheme.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from .tls import client_tls_from_env, wrap_client


@dataclass
class Upstream:
    scheme: str        # "http" or "https"
    host: str
    port: int
    tls: bool

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @classmethod
    def from_env(cls, default: str = "http://127.0.0.1:8080") -> "Upstream":
        url = os.environ.get("SLIMTOKEN_UPSTREAM", default)
        p = urlparse(url)
        scheme = (p.scheme or "http").lower()
        host = p.hostname or "127.0.0.1"
        port = p.port or (443 if scheme == "https" else 80)
        return cls(scheme=scheme, host=host, port=port, tls=(scheme == "https"))

    def connect_raw(self, timeout: float = 30.0) -> socket.socket:
        """Connect a raw socket to the upstream (TLS-wrapped for https).

        Used by the local streaming path. For https this still works but most
        cloud calls go through http.client via :meth:`https_connection`.
        """
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        if self.tls:
            ctx = client_tls_from_env() or __import__("ssl").create_default_context()
            sock = wrap_client(sock, self.host, ctx)
        return sock

    def https_connection(self, timeout: float = 30.0):
        """An http.client.HTTPSConnection for the cloud path (TLS native)."""
        import http.client
        ctx = client_tls_from_env() or __import__("ssl").create_default_context()
        return http.client.HTTPSConnection(self.host, self.port,
                                           context=ctx, timeout=timeout)