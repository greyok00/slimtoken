
from __future__ import annotations

import socket
from typing import Iterator, Union

SocketLike = Union[socket.socket, object]


def drain_lines(
    conn: SocketLike,
    max_bytes: int = 8 * 1024 * 1024,
    timeout: float = 15.0,
    chunk_size: int = 65536,
) -> Iterator[str]:

    if hasattr(conn, "settimeout"):
        conn.settimeout(timeout)

    buf = bytearray()
    total = 0

    while True:
        try:
            chunk = conn.recv(chunk_size)
        except socket.timeout:

            if buf:
                yield buf.decode("utf-8", errors="replace")
            return
        except OSError:

            if buf:
                yield buf.decode("utf-8", errors="replace")
            return

        if not chunk:

            if buf:
                yield buf.decode("utf-8", errors="replace")
            return

        total += len(chunk)
        if total > max_bytes:
            raise ValueError(
                f"drain_lines exceeded max_bytes={max_bytes} "
                f"(read {total} so far). Aborting stream."
            )

        buf.extend(chunk)


        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(buf[:nl]).decode("utf-8", errors="replace")
            del buf[: nl + 1]
            yield line


__all__ = ["drain_lines"]
