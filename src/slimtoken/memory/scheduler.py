
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union




_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


def parse(expr: str, now: Optional[datetime] = None) -> bool:

    if now is None:
        now = datetime.now()

    expr = expr.strip()
    if expr in _ALIASES:
        expr = _ALIASES[expr]

    fields = expr.split()
    if len(fields) != 5:
        return False

    minute, hour, dom, mon, dow = fields

    cron_dow = (now.weekday() + 1) % 7
    values = [now.minute, now.hour, now.day, now.month, cron_dow]

    for fld, val in zip([minute, hour, dom, mon, dow], values):
        if fld == "*":
            continue
        if "," in fld:
            opts = [int(x) for x in fld.split(",")]
            if val not in opts:
                return False
        elif "/" in fld:
            base, step = fld.split("/", 1)
            start = 0 if base == "*" else int(base)
            if val < start or (val - start) % int(step) != 0:
                return False
        elif "-" in fld:
            lo, hi = [int(x) for x in fld.split("-", 1)]
            if val < lo or val > hi:
                return False
        else:
            try:
                if int(fld) != val:
                    return False
            except ValueError:
                return False
    return True




def _atomic_write_json(path: Path, data: Any) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, default: Any = None) -> Any:
    if default is None:
        default = []
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Schedule:


    def __init__(self, dir: Union[str, Path], name: str = "schedule"):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{name}.json"
        self._entries = _read_json(self.path, [])
        if not isinstance(self._entries, list):
            self._entries = []



    def add(
        self,
        name: str,
        kind: str,
        expr: str,
        payload: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:

        entry = {
            "name": name,
            "kind": kind,
            "expr": expr,
            "payload": payload or {},
            "enabled": enabled,
            "last_run": None,
            "created_at": datetime.now().isoformat(),
        }

        self._entries = [e for e in self._entries if e.get("name") != name]
        self._entries.append(entry)
        self._save()
        return entry

    def remove(self, name: str) -> bool:

        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get("name") != name]
        if len(self._entries) < before:
            self._save()
            return True
        return False

    def list(self) -> List[Dict[str, Any]]:

        return list(self._entries)

    def enable(self, name: str, enabled: bool = True) -> bool:

        for e in self._entries:
            if e.get("name") == name:
                e["enabled"] = enabled
                self._save()
                return True
        return False



    def run_due(
        self,
        now: Optional[datetime] = None,
        callback: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> int:

        if now is None:
            now = datetime.now()
        bucket = now.strftime("%Y%m%d%H%M")
        dispatched = 0
        mutated = False

        for e in self._entries:
            if not e.get("enabled", True):
                continue
            expr = e.get("expr", "")
            if not expr:
                continue


            last_run = e.get("last_run")
            if last_run:
                try:
                    if datetime.fromisoformat(last_run).strftime("%Y%m%d%H%M") == bucket:
                        continue
                except Exception:
                    pass

            if not parse(expr, now):
                continue


            if callback is None:

                dispatched += 1
                continue

            try:
                ok = callback(e)
            except Exception:
                ok = False

            if ok:
                e["last_run"] = now.isoformat()
                mutated = True
                dispatched += 1

        if mutated:
            self._save()
        return dispatched



    def _save(self) -> None:
        _atomic_write_json(self.path, self._entries)


__all__ = ["parse", "Schedule"]
