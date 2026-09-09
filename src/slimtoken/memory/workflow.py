
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Union

from .dag import Task, DAGScheduler, DAGResult, BatchGroup, StepStatus


class TaskExecutor(Protocol):


    def execute(self, task: Task) -> bool: ...



ExecutorFn = Callable[[Task], bool]


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


class Workflow:


    def __init__(
        self,
        dir: Union[str, Path],
        name: str = "workflow",
        executor: Optional[ExecutorFn] = None,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{name}.json"
        self.executor = executor
        self.on_progress = on_progress
        self.progress_events: List[Dict[str, Any]] = []

    def set_executor(self, executor: ExecutorFn) -> None:

        self.executor = executor



    def run(self, plan: List[Task], on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:

        if self.executor is None:
            raise RuntimeError(
                "Workflow.run() requires an executor. "
                "Pass one at construction or via set_executor()."
            )
        if not plan:
            return {"status": "empty", "total_tasks": 0, "completed": 0, "failed": 0, "results": {}}

        cb = on_progress or self.on_progress


        dag = DAGScheduler()
        for task in plan:
            dag.add_task(task)


        cycle = dag.detect_cycles()
        if cycle:
            self._emit(cb, {"phase": "validation", "status": "failed", "reason": "cycle", "tasks": cycle})
            return {
                "status": "failed",
                "reason": "cycle_detected",
                "cycle_nodes": cycle,
                "total_tasks": len(plan),
                "completed": 0,
                "failed": 0,
                "results": {},
            }


        self._save_plan(plan)

        completed: set = set()
        failed: set = set()
        results: Dict[str, Dict[str, Any]] = {}



        for task in plan:
            if task.status == StepStatus.COMPLETED:
                completed.add(task.id)
            elif task.status == StepStatus.FAILED:
                failed.add(task.id)


        max_iterations = len(plan) + 1
        iteration = 0
        while len(completed) + len(failed) < len(plan):
            iteration += 1
            if iteration > max_iterations:

                self._emit(cb, {"phase": "execution", "status": "failed", "reason": "infinite_loop"})
                break

            ready = dag.get_ready_tasks(completed | failed)

            runnable: List[Task] = []
            for t in ready:
                if any(d in failed for d in t.depends_on):
                    t.status = StepStatus.SKIPPED
                    failed.add(t.id)
                    results[t.id] = {"name": t.name, "status": "skipped", "reason": "dependency_failed"}
                    self._emit(cb, {"phase": "execution", "task_id": t.id, "status": "skipped"})
                else:
                    runnable.append(t)

            if not runnable:


                stuck = [t.id for t in dag.tasks.values() if t.status == StepStatus.PENDING]
                for tid in stuck:
                    failed.add(tid)
                    results[tid] = {"name": dag.tasks[tid].name, "status": "failed", "reason": "no_runnable_deps"}
                    self._emit(cb, {"phase": "execution", "task_id": tid, "status": "failed", "reason": "no_runnable_deps"})
                break


            for task in runnable:
                task.status = StepStatus.RUNNING
                self._emit(cb, {"phase": "execution", "task_id": task.id, "status": "running", "name": task.name})
                try:
                    ok = self.executor(task)
                except Exception as e:
                    ok = False
                    task.error = str(e)[:500]

                if ok:
                    task.status = StepStatus.COMPLETED
                    completed.add(task.id)
                    results[task.id] = {
                        "name": task.name,
                        "kind": task.kind.name,
                        "status": "completed",
                        "result": task.result,
                    }
                    self._emit(cb, {"phase": "execution", "task_id": task.id, "status": "completed"})
                else:
                    task.status = StepStatus.FAILED
                    failed.add(task.id)
                    results[task.id] = {
                        "name": task.name,
                        "kind": task.kind.name,
                        "status": "failed",
                        "error": task.error,
                    }
                    self._emit(cb, {"phase": "execution", "task_id": task.id, "status": "failed", "error": task.error})


            self._save_plan(plan)

        return {
            "status": "completed" if not failed else "completed_with_failures",
            "total_tasks": len(plan),
            "completed": len(completed),
            "failed": len(failed),
            "results": results,
        }

    def get_status(self) -> Dict[str, Any]:

        data = _read_json(self.path)
        if not data:
            return {"status": "no_workflow"}
        tasks = data.get("tasks", [])
        return {
            "status": "in_progress",
            "total_tasks": len(tasks),
            "completed": sum(1 for t in tasks if t.get("status") == "COMPLETED"),
            "failed": sum(1 for t in tasks if t.get("status") == "FAILED"),
            "running": sum(1 for t in tasks if t.get("status") == "RUNNING"),
            "pending": sum(1 for t in tasks if t.get("status") == "PENDING"),
            "tasks": [
                {"id": t["id"], "name": t["name"], "status": t["status"], "kind": t["kind"]}
                for t in tasks
            ],
        }

    def clear(self) -> bool:

        if self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                return False
            return True
        return False



    def _save_plan(self, plan: List[Task]) -> None:
        data = {
            "tasks": [
                {
                    "id": t.id,
                    "name": t.name,
                    "kind": t.kind.name,
                    "prompt": t.prompt,
                    "depends_on": t.depends_on,
                    "status": t.status.name,
                    "priority": t.priority,
                    "result": t.result,
                    "error": t.error,
                }
                for t in plan
            ],
            "updated_at": datetime.now().isoformat(),
        }
        _atomic_write_json(self.path, data)

    def _emit(self, cb: Optional[Callable[[Dict[str, Any]], None]], event: Dict[str, Any]) -> None:
        self.progress_events.append(event)
        if cb:
            try:
                cb(event)
            except Exception:
                pass


__all__ = [
    "TaskExecutor",
    "ExecutorFn",
    "Workflow",
]
