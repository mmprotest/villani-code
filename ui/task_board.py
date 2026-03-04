from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class Task:
    id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class TaskEvent:
    kind: str
    task_id: str | None
    detail: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.timeline: list[TaskEvent] = []

    def create_task(self, task_id: str, title: str, metadata: dict[str, Any] | None = None) -> Task:
        task = Task(id=task_id, title=title, metadata=metadata or {})
        self.tasks[task_id] = task
        self.timeline.append(TaskEvent(kind="TaskCreated", task_id=task_id, detail=title))
        return task

    def update_status(self, task_id: str, status: TaskStatus, progress: float | None = None) -> None:
        task = self.tasks[task_id]
        task.status = status
        if progress is not None:
            task.progress = min(1.0, max(0.0, progress))
        task.updated_at = datetime.now(timezone.utc)
        self.timeline.append(TaskEvent(kind="TaskStatus", task_id=task_id, detail=status.value))

    def record_event(self, kind: str, detail: str, task_id: str | None = None) -> None:
        self.timeline.append(TaskEvent(kind=kind, task_id=task_id, detail=detail))

    def recent_events(self, limit: int = 20) -> list[TaskEvent]:
        return self.timeline[-limit:]
