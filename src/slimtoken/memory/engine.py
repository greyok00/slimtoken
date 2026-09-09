
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


MEMORY_DIR = Path.home() / ".config" / "cortexllm"
HOT_DIR = MEMORY_DIR / "memory" / "hot"




WARM_DIR = MEMORY_DIR / "memory" / "warm"
COLD_DIR = MEMORY_DIR / "memory" / "cold"
ENTERPRISE_DB = MEMORY_DIR / "cortexllm.db"


PIPE_BUF = 4096


def _now_ts() -> str:

    return time.strftime("%Y-%m-%d %H:%M:%S")



def atomic_append(file_path: Union[str, Path], line: str) -> None:

    fp = Path(file_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(fp, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8") if isinstance(line, str) else line)
    finally:
        os.close(fd)


def atomic_append_bytes(file_path: Union[str, Path], data: bytes) -> None:

    fp = Path(file_path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(fp, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)



def _message(role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    msg = {"role": role, "content": content, "timestamp": _now_ts()}
    if meta:
        msg["metadata"] = meta
    return msg


def append(role: str, content: str, *, platform: str = "default",
           meta: Optional[Dict[str, Any]] = None,
           tiers: Tuple[str, ...] = ("hot",)) -> Path:

    msg = _message(role, content, meta)
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    if "hot" in tiers:
        atomic_append(HOT_DIR / f"{platform}.jsonl", line)
    return HOT_DIR / f"{platform}.jsonl"


def read_last(n: int = 5, *, platform: str = "default") -> List[Dict[str, Any]]:

    hot_file = HOT_DIR / f"{platform}.jsonl"
    if not hot_file.exists():
        return []
    try:
        size = hot_file.stat().st_size
        with open(hot_file, "rb") as f:
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace")
        lines = [l for l in tail.split("\n") if l.strip()][-n:]
        return [json.loads(l) for l in lines]
    except (OSError, ValueError):
        return []


def search(query: str, *, tier: str = "hot", platform: str = "default",
           limit: int = 10) -> List[Dict[str, Any]]:

    if not query:
        return []
    q = query.lower()
    path = HOT_DIR / f"{platform}.jsonl"
    if not path.exists():
        return []
    matches = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if q in line.lower():
                    try:
                        entry = json.loads(line)
                        entry["line_no"] = i
                        matches.append(entry)
                        if len(matches) >= limit:
                            break
                    except ValueError:
                        continue
    except OSError:
        pass
    return matches



def cold_list() -> List[str]:

    if not COLD_DIR.exists():
        return []
    return sorted(p.stem for p in COLD_DIR.glob("*.json"))


def cold_get(category: str) -> Dict[str, Any]:

    path = COLD_DIR / f"{category}.json"
    if not path.exists():
        return {"category": category, "entries": []}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"category": category, "entries": []}


def write_cold(category: str, knowledge: Dict[str, Any], *,
               source: str = "default", replace: bool = False) -> Path:

    import tempfile
    path = COLD_DIR / f"{category}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                data = {"category": category, "entries": []}
        except (OSError, ValueError):
            data = {"category": category, "entries": []}
    else:
        data = {"category": category, "entries": []}
    if "entries" not in data:
        data["entries"] = []
    entry = {
        "timestamp": _now_ts(),
        "source": source,
        "knowledge": knowledge,
    }
    if replace:
        data["entries"] = [entry]
    else:
        data["entries"].append(entry)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cold-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path



def hot_to_warm_sync(platforms: Tuple[str, ...] = ("claude", "default")) -> int:

    return 0


__all__ = [
    "MEMORY_DIR", "HOT_DIR", "WARM_DIR", "COLD_DIR", "ENTERPRISE_DB",
    "atomic_append", "atomic_append_bytes",
    "append", "read_last", "search",
    "cold_list", "cold_get", "write_cold",
    "hot_to_warm_sync",
    "PIPE_BUF",
]
