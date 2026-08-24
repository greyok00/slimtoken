
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from .tls import client_tls_from_env, wrap_client


@dataclass
class Upstream:
    scheme: str
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

        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        if self.tls:
            ctx = client_tls_from_env() or __import__("ssl").create_default_context()
            sock = wrap_client(sock, self.host, ctx)
        return sock

    def https_connection(self, timeout: float = 30.0):

        import http.client
        ctx = client_tls_from_env() or __import__("ssl").create_default_context()
        return http.client.HTTPSConnection(self.host, self.port,
                                           context=ctx, timeout=timeout)