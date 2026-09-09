
from __future__ import annotations

import json
import os
import tempfile
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
        default = {}
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


class Plan:


    def __init__(self, dir: Union[str, Path], name: str = "plan"):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{name}.json"



    def set(
        self,
        name: str,
        total_steps: int,
        steps: Optional[List[str]] = None,
        context: str = "",
    ) -> Dict[str, Any]:

        if total_steps < 1:
            raise ValueError("total_steps must be >= 1")
        plan = {
            "name": name,
            "total_steps": total_steps,
            "current_step": 0,
            "context": context,
            "steps": steps if steps else [f"Step {i + 1}" for i in range(total_steps)],
            "step_status": ["pending"] * total_steps,
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "completed": False,
        }
        if len(plan["steps"]) != total_steps:
            raise ValueError(
                f"steps has {len(plan['steps'])} entries; expected {total_steps}"
            )
        self._save(plan)
        return plan

    def advance(self, n: Optional[int] = None) -> Dict[str, Any]:

        plan = _read_json(self.path)
        if not plan:
            return {"error": "No plan set. Use set() first."}
        if plan.get("completed"):
            return {"error": "Plan already completed."}

        if n is not None:
            if n < 1 or n > plan["total_steps"]:
                return {"error": f"Step {n} out of range (1-{plan['total_steps']})"}
            plan["current_step"] = n
        else:
            plan["current_step"] += 1

        if plan["current_step"] > plan["total_steps"]:
            plan["completed"] = True
            plan["current_step"] = plan["total_steps"]
            plan["step_status"] = ["done"] * plan["total_steps"]
            plan["updated_at"] = datetime.now().isoformat()
            self._save(plan)
            return plan



        plan["step_status"] = [
            "done" if i + 1 < plan["current_step"] else
            "in_progress" if i + 1 == plan["current_step"] else
            "pending"
            for i in range(plan["total_steps"])
        ]
        plan["updated_at"] = datetime.now().isoformat()
        self._save(plan)
        return plan

    def complete(self) -> Dict[str, Any]:

        plan = _read_json(self.path)
        if not plan:
            return {"error": "No plan set."}
        plan["completed"] = True
        plan["step_status"] = ["done"] * plan["total_steps"]
        plan["current_step"] = plan["total_steps"]
        plan["updated_at"] = datetime.now().isoformat()
        self._save(plan)
        return plan

    def clear(self) -> bool:

        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                return False
            return True
        return False



    def status(self) -> Dict[str, Any]:

        plan = _read_json(self.path)
        if not plan:
            return {"error": "No plan set."}
        return plan



    def _save(self, plan: Dict[str, Any]) -> None:
        _atomic_write_json(self.path, plan)


__all__ = ["Plan"]
