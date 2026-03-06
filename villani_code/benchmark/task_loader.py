from __future__ import annotations

import json
from pathlib import Path

from villani_code.benchmark.models import BenchmarkTask


def load_benchmark_task(path: Path) -> BenchmarkTask:
    payload = json.loads(path.read_text(encoding="utf-8"))
    task = BenchmarkTask.model_validate(payload)
    if not task.id:
        raise ValueError(f"Task in {path} is missing id")
    return task


def load_benchmark_tasks(tasks_dir: Path, task_id: str | None = None) -> list[BenchmarkTask]:
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Tasks directory does not exist: {tasks_dir}")
    task_files = sorted(tasks_dir.glob("*.json"))
    tasks = [load_benchmark_task(path) for path in task_files]
    if task_id is not None:
        tasks = [task for task in tasks if task.id == task_id]
        if not tasks:
            raise ValueError(f"Task id '{task_id}' not found in {tasks_dir}")
    return tasks
