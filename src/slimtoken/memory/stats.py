
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import engine as _engine


def _count_lines(path: Path) -> int:

    if not path.exists():
        return 0
    n = 0
    last = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            n += chunk.count(b"\n")
            last = chunk



    if last and not last.endswith(b"\n"):
        n += 1
    return n


def _bytes(path: Path) -> int:

    try:
        return path.stat().st_size
    except OSError:
        return 0


def _per_platform(platform: Optional[str], tier_dir: Path, ext: str) -> Dict[str, Dict[str, int]]:

    out: Dict[str, Dict[str, int]] = {}
    if not tier_dir.exists():
        return out
    if platform:
        candidates = [tier_dir / f"{platform}{ext}"]
    else:
        candidates = sorted(tier_dir.glob(f"*{ext}"))
    for f in candidates:
        if not f.exists():
            continue
        name = f.name[: -len(ext)] if ext else f.name
        out[name] = {
            "bytes": _bytes(f),
            "entries": _count_lines(f),
        }
    return out


def stats(
    platform: Optional[str] = None,
    dir: Union[str, Path, None] = None,
) -> Dict[str, Any]:

    if dir is not None:
        base = Path(dir)
        hot_dir = base / "hot"
        cold_dir = base / "cold"
    else:


        hot_dir = _engine.HOT_DIR
        cold_dir = _engine.COLD_DIR

    hot_by_plat = _per_platform(platform, hot_dir, ".jsonl")

    hot_bytes = sum(p["bytes"] for p in hot_by_plat.values())
    hot_entries = sum(p["entries"] for p in hot_by_plat.values())

    cold_categories: List[str] = []
    cold_files: List[Dict[str, Any]] = []
    cold_bytes_total = 0
    if cold_dir.exists():
        for f in sorted(cold_dir.glob("*.json")):
            sz = _bytes(f)
            cold_bytes_total += sz
            cold_files.append({"category": f.stem, "bytes": sz})
            cold_categories.append(f.stem)



    total_chars = hot_bytes + cold_bytes_total
    estimated_tokens = total_chars // 4

    return {
        "hot": {
            "bytes": hot_bytes,
            "entries": hot_entries,
            "by_platform": hot_by_plat,
        },


        "warm": {
            "bytes": 0,
            "entries": 0,
            "by_platform": {},
        },
        "cold": {
            "categories": cold_categories,
            "files": cold_files,
            "bytes": cold_bytes_total,
        },
        "estimated_tokens": estimated_tokens,
    }


def estimate_tokens(text: str) -> int:

    if not text:
        return 0
    return max(1, len(text) // 4)


__all__ = ["stats", "estimate_tokens"]
