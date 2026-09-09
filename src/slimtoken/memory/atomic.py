
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union


PIPE_BUF = 4096


def atomic_append(path: Union[str, Path], line: str) -> int:

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = line.encode("utf-8") if isinstance(line, str) else line
    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        return os.write(fd, data)
    finally:
        os.close(fd)


def atomic_append_bytes(path: Union[str, Path], data: bytes) -> int:

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        return os.write(fd, data)
    finally:
        os.close(fd)







@contextmanager
def flock_acquire(path: Union[str, Path]) -> Iterator[int]:

    try:
        import fcntl
    except ImportError as e:
        raise NotImplementedError(
            "flock_acquire requires fcntl (POSIX). On Windows, use a "
            "different coordination primitive (msvcrt.locking or a sentinel file)."
        ) from e

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


__all__ = [
    "PIPE_BUF",
    "atomic_append",
    "atomic_append_bytes",
    "flock_acquire",
]
