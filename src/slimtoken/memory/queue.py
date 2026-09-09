
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


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


class Queue:


    def __init__(self, dir: Union[str, Path], name: str = "queue"):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{name}.json"
        self._items: List[Dict[str, Any]] = _read_json(self.path, [])
        if not isinstance(self._items, list):
            self._items = []
        self._lock = threading.Lock()



    def add(self, item: Dict[str, Any], status: str = "queued") -> Dict[str, Any]:

        with self._lock:
            entry = dict(item)
            entry.setdefault("id", uuid.uuid4().hex[:12])
            entry["created_at"] = datetime.now().isoformat()
            entry["status"] = status
            self._items.append(entry)
            self._save_unlocked()
            return entry

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)

    def pending(self) -> int:

        with self._lock:
            return sum(1 for i in self._items if i.get("status") == "queued")

    def remove(self, item_id: str) -> bool:

        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.get("id") != item_id]
            if len(self._items) < before:
                self._save_unlocked()
                return True
            return False

    def clear(self) -> int:

        with self._lock:
            n = len(self._items)
            self._items = []
            self._save_unlocked()
            return n

    def update_status(
        self,
        item_id: str,
        status: str,
        **fields: Any,
    ) -> bool:

        with self._lock:
            for i in self._items:
                if i.get("id") == item_id:
                    i["status"] = status
                    i["updated_at"] = datetime.now().isoformat()
                    for k, v in fields.items():
                        i[k] = v
                    self._save_unlocked()
                    return True
            return False

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for i in self._items:
                if i.get("id") == item_id:
                    return dict(i)
            return None



    def pending_items(self) -> List[Dict[str, Any]]:

        with self._lock:
            return [dict(i) for i in self._items if i.get("status") == "queued"]

    def mark_in_progress(self, item_id: str) -> bool:
        return self.update_status(item_id, "in_progress", started_at=datetime.now().isoformat())

    def mark_completed(self, item_id: str, result: Any = None) -> bool:
        return self.update_status(
            item_id, "completed",
            completed_at=datetime.now().isoformat(),
            result=result,
        )

    def mark_failed(self, item_id: str, error: str = "") -> bool:
        return self.update_status(
            item_id, "failed",
            completed_at=datetime.now().isoformat(),
            error=error[:500],
        )



    def _save_unlocked(self) -> None:
        _atomic_write_json(self.path, self._items)


__all__ = ["Queue"]
