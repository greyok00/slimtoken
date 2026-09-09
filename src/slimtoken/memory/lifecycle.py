
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union


class SingleInstanceError(RuntimeError):


    def __init__(self, pidfile: Path, holder_pid: Optional[int]):
        self.pidfile = pidfile
        self.holder_pid = holder_pid
        msg = f"Another instance holds {pidfile}"
        if holder_pid is not None:
            msg += f" (pid {holder_pid})"
        super().__init__(msg)


def read_pid(pidfile: Union[str, Path]) -> Optional[int]:

    p = Path(pidfile)
    if not p.exists():
        return None
    try:
        pid = int(p.read_text().strip())
    except (ValueError, OSError):
        try:
            p.unlink()
        except OSError:
            pass
        return None

    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:

        try:
            p.unlink()
        except OSError:
            pass
        return None
    except PermissionError:


        return pid
    except OSError:

        try:
            p.unlink()
        except OSError:
            pass
        return None


def write_pid(pidfile: Union[str, Path], pid: Optional[int] = None) -> None:

    p = Path(pidfile)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(pid if pid is not None else os.getpid()))


def remove_pid(pidfile: Union[str, Path]) -> None:

    try:
        Path(pidfile).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


@contextmanager
def SingleInstance(
    pidfile: Union[str, Path],
    blocking: bool = False,
) -> Iterator[int]:

    try:
        import fcntl
    except ImportError as e:
        raise NotImplementedError(
            "SingleInstance requires fcntl (POSIX). On Windows, use a "
            "named mutex or a sentinel-file-based lock."
        ) from e

    p = Path(pidfile)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        op = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, op)
        except OSError as e:

            other_pid = read_pid(p)
            try:
                os.close(fd)
            except OSError:
                pass
            raise SingleInstanceError(p, other_pid) from e


        write_pid(p, os.getpid())
        try:
            yield fd
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            remove_pid(p)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def daemonize(
    logs_dir: Union[str, Path],
    chdir: Union[str, Path, None] = None,
) -> None:

    if sys.platform == "win32":
        raise NotImplementedError("daemonize() is Unix-only. Use a Windows service.")

    logs = Path(logs_dir)
    logs.mkdir(parents=True, exist_ok=True)


    pid = os.fork()
    if pid > 0:

        os._exit(0)

    os.setsid()


    pid = os.fork()
    if pid > 0:
        os._exit(0)


    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "rb", 0) as null_in:
        os.dup2(null_in.fileno(), 0)
    log_out = open(logs / "daemon.out.log", "ab", 0)
    log_err = open(logs / "daemon.err.log", "ab", 0)
    os.dup2(log_out.fileno(), 1)
    os.dup2(log_err.fileno(), 2)

    if chdir is not None:
        os.chdir(str(chdir))


__all__ = [
    "SingleInstance",
    "SingleInstanceError",
    "read_pid",
    "write_pid",
    "remove_pid",
    "daemonize",
]
