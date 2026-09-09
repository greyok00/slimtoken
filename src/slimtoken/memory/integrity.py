
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Union


def check(path: Union[str, Path]) -> Dict[str, Any]:

    p = Path(path)
    result: Dict[str, Any] = {
        "path": str(p),
        "exists": p.exists(),
        "ok": True,
        "line_count": 0,
        "valid_lines": 0,
        "bad_lines": 0,
        "truncated": False,
        "last_ts": None,
        "last_role": None,
        "first_error": None,
    }
    if not p.exists():
        return result

    try:
        size = p.stat().st_size
    except OSError:
        result["ok"] = False
        return result

    if size == 0:
        return result

    line_count = 0
    valid = 0
    bad = 0
    first_error: Dict[str, Any] = None  # type: ignore
    last_ts = None
    last_role = None

    with open(p, "rb") as f:
        for line_no, raw in enumerate(f, 1):
            line_count += 1
            stripped = raw.rstrip(b"\r\n")
            if not stripped:

                continue
            try:
                obj = json.loads(stripped.decode("utf-8", errors="replace"))
            except (ValueError, UnicodeDecodeError) as e:
                bad += 1
                if first_error is None:
                    first_error = {
                        "line_no": line_no,
                        "reason": str(e),
                        "preview": stripped[:120].decode("utf-8", errors="replace"),
                    }
                continue
            valid += 1
            if isinstance(obj, dict):
                ts = obj.get("ts") or obj.get("timestamp") or obj.get("time")
                role = obj.get("role")
                if ts is not None:
                    last_ts = str(ts)
                if role is not None:
                    last_role = str(role)




    try:
        with open(p, "rb") as f2:
            if size > 0:
                f2.seek(size - 1)
                tail = f2.read(1)
                truncated = tail != b"\n"
            else:
                truncated = False
    except OSError:
        truncated = False

    result["line_count"] = line_count
    result["valid_lines"] = valid
    result["bad_lines"] = bad
    result["truncated"] = truncated
    result["last_ts"] = last_ts
    result["last_role"] = last_role
    result["first_error"] = first_error
    result["ok"] = bad == 0
    return result


def quick(path: Union[str, Path]) -> bool:

    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return True
    try:
        with open(p, "rb") as f:
            for raw in f:
                s = raw.strip()
                if not s:
                    continue
                try:
                    json.loads(s)
                except (ValueError, UnicodeDecodeError):
                    return False
        return True
    except OSError:
        return False


def validate_lines(lines: Iterable[str]) -> Dict[str, Any]:

    line_count = 0
    valid = 0
    bad = 0
    first_error: Dict[str, Any] = None  # type: ignore
    last_ts = None
    last_role = None

    for line_no, line in enumerate(lines, 1):
        line_count += 1
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (ValueError, TypeError) as e:
            bad += 1
            if first_error is None:
                first_error = {
                    "line_no": line_no,
                    "reason": str(e),
                    "preview": s[:120],
                }
            continue
        valid += 1
        if isinstance(obj, dict):
            ts = obj.get("ts") or obj.get("timestamp") or obj.get("time")
            role = obj.get("role")
            if ts is not None:
                last_ts = str(ts)
            if role is not None:
                last_role = str(role)

    return {
        "line_count": line_count,
        "valid_lines": valid,
        "bad_lines": bad,
        "last_ts": last_ts,
        "last_role": last_role,
        "first_error": first_error,
        "ok": bad == 0,
    }


__all__ = ["check", "quick", "validate_lines"]
