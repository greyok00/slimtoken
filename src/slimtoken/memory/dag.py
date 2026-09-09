
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Union


class StepKind(Enum):

    COMMAND = auto()
    LLM = auto()
    WEBHOOK = auto()
    NOTIFY = auto()


class StepStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class Task:

    id: str
    name: str
    kind: StepKind
    prompt: str
    depends_on: List[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    priority: int = 0
    result: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Union[str, int, float, bool]] = field(default_factory=dict)


@dataclass
class BatchGroup:

    kind: StepKind
    tasks: List[Task]
    batch_id: str


@dataclass
class DAGResult:

    order: List[str]
    batches: List[BatchGroup]
    has_cycles: bool = False
    cycle_nodes: Optional[List[str]] = None


class DAGScheduler:


    def __init__(self) -> None:
        self.tasks: Dict[str, Task] = {}
        self.dependencies: Dict[str, List[str]] = {}
        self.dependents: Dict[str, List[str]] = {}

    def add_task(self, task: Task) -> None:

        self.tasks[task.id] = task
        self.dependencies[task.id] = list(task.depends_on)
        for dep_id in task.depends_on:
            self.dependents.setdefault(dep_id, []).append(task.id)

    def remove_task(self, task_id: str) -> bool:

        if task_id not in self.tasks:
            return False
        del self.tasks[task_id]
        self.dependencies.pop(task_id, None)
        self.dependents.pop(task_id, None)

        for tid, deps in list(self.dependencies.items()):
            if task_id in deps:
                self.dependencies[tid] = [d for d in deps if d != task_id]
        for deps in self.dependents.values():
            if task_id in deps:
                deps.remove(task_id)
        return True

    def topological_sort(self) -> List[str]:

        in_degree = {tid: len(deps) for tid, deps in self.dependencies.items()}
        queue: deque = deque([tid for tid, deg in in_degree.items() if deg == 0])
        result: List[str] = []

        while queue:
            tid = queue.popleft()
            result.append(tid)
            for dep_id in self.dependents.get(tid, []):
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    queue.append(dep_id)

        if len(result) != len(self.tasks):
            cycle = set(self.tasks.keys()) - set(result)
            raise ValueError(f"Cycle detected in DAG involving tasks: {sorted(cycle)}")
        return result

    def detect_cycles(self) -> Optional[List[str]]:

        try:
            self.topological_sort()
            return None
        except ValueError as e:

            msg = str(e)
            if "tasks: " in msg:
                ids = msg.split("tasks: ", 1)[1].strip("[]")
                return [s.strip().strip("'") for s in ids.split(",")]
            return []

    def get_ready_tasks(self, completed: Set[str]) -> List[Task]:

        ready: List[Task] = []
        for tid, task in self.tasks.items():
            if task.status != StepStatus.PENDING:
                continue
            deps = self.dependencies.get(tid, [])
            if all(d in completed for d in deps):
                ready.append(task)
        return ready

    def batch_by_kind(self, tasks: List[Task]) -> List[BatchGroup]:

        groups: Dict[StepKind, List[Task]] = defaultdict(list)
        for task in tasks:
            groups[task.kind].append(task)

        return [
            BatchGroup(
                kind=kind,
                tasks=list(group_tasks),
                batch_id=f"batch_{kind.name}_{i}",
            )
            for i, (kind, group_tasks) in enumerate(
                sorted(groups.items(), key=lambda x: x[0].name)
            )
        ]

    def optimize_schedule(self) -> DAGResult:

        try:
            order = self.topological_sort()
        except ValueError:
            cycle = self.detect_cycles() or []
            return DAGResult(
                order=[],
                batches=[],
                has_cycles=True,
                cycle_nodes=cycle,
            )


        batches: List[BatchGroup] = []
        current_kind: Optional[StepKind] = None
        current_batch: List[Task] = []

        for tid in order:
            task = self.tasks[tid]
            if task.kind != current_kind:
                if current_batch:
                    batches.append(BatchGroup(
                        kind=current_kind,
                        tasks=current_batch,
                        batch_id=f"batch_{current_kind.name}_{len(batches)}",
                    ))
                current_kind = task.kind
                current_batch = [task]
            else:
                current_batch.append(task)

        if current_batch:
            batches.append(BatchGroup(
                kind=current_kind,
                tasks=current_batch,
                batch_id=f"batch_{current_kind.name}_{len(batches)}",
            ))

        return DAGResult(order=order, batches=batches, has_cycles=False)


__all__ = [
    "Task",
    "StepKind",
    "StepStatus",
    "BatchGroup",
    "DAGResult",
    "DAGScheduler",
]
