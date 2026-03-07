from __future__ import annotations

import json
from pathlib import Path

from villani_code.benchmark.models import BenchmarkTask, BenchmarkTaskPack


REQUIRED_PACK_FIELDS = {"name", "classification", "description", "comparison_suitability", "fairness_classification"}


def load_benchmark_task(path: Path) -> BenchmarkTask:
    payload = json.loads(path.read_text(encoding="utf-8"))
    task = BenchmarkTask.model_validate(payload)
    if not task.id:
        raise ValueError(f"Task in {path} is missing id")
    return task


def resolve_tasks_dir(tasks_dir: Path) -> Path:
    if tasks_dir.exists():
        return tasks_dir
    legacy = Path(str(tasks_dir).replace("benchmark_tasks/villani_code", "benchmark_tasks/internal_regressions"))
    if legacy.exists():
        return legacy
    raise FileNotFoundError(f"Tasks directory does not exist: {tasks_dir}")


def load_task_pack_metadata(tasks_dir: Path) -> BenchmarkTaskPack:
    resolved = resolve_tasks_dir(tasks_dir)
    metadata_file = resolved / "pack.json"
    if metadata_file.exists():
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
        missing = REQUIRED_PACK_FIELDS - set(payload)
        if missing:
            raise ValueError(f"Pack metadata missing required fields: {sorted(missing)}")
        return BenchmarkTaskPack.model_validate(payload)
    return BenchmarkTaskPack(
        name=resolved.name,
        classification="exploratory",
        description="Unclassified task pack.",
        comparison_suitability="unknown",
        fairness_classification="mixed",
    )


def load_benchmark_tasks(tasks_dir: Path, task_id: str | None = None) -> list[BenchmarkTask]:
    resolved = resolve_tasks_dir(tasks_dir)
    task_files = sorted(path for path in resolved.glob("*.json") if path.name != "pack.json")
    tasks = [load_benchmark_task(path) for path in task_files]
    if task_id is not None:
        tasks = [task for task in tasks if task.id == task_id]
        if not tasks:
            raise ValueError(f"Task id '{task_id}' not found in {resolved}")
    return tasks


def lint_task_pack(tasks_dir: Path) -> list[str]:
    errors: list[str] = []
    resolved = resolve_tasks_dir(tasks_dir)
    try:
        load_task_pack_metadata(resolved)
    except Exception as exc:
        errors.append(str(exc))
        return errors

    tasks = load_benchmark_tasks(resolved)
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for task in tasks:
        if task.id in seen_ids:
            errors.append(f"Duplicate task id: {task.id}")
        seen_ids.add(task.id)
        if task.name in seen_titles:
            errors.append(f"Duplicate task title: {task.name}")
        seen_titles.add(task.name)
        if not task.instruction.strip():
            errors.append(f"Task {task.id} has empty instruction")
        if not task.validation_checks:
            errors.append(f"Task {task.id} has no validation checks")
        if not task.tags:
            errors.append(f"Task {task.id} has empty tags")
        if not task.difficulty:
            errors.append(f"Task {task.id} missing difficulty")
    return errors
