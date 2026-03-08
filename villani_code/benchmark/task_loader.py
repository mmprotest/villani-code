from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from villani_code.benchmark.models import BenchmarkTask


class TaskLoadError(RuntimeError):
    pass


def _read_prompt(prompt_file: Path) -> str:
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if "\n" in prompt:
        raise TaskLoadError(f"{prompt_file} must contain exactly one short instruction")
    if not prompt:
        raise TaskLoadError(f"{prompt_file} is empty")
    return prompt


def load_task(task_dir: Path) -> BenchmarkTask:
    task_yaml = task_dir / "task.yaml"
    prompt_txt = task_dir / "prompt.txt"
    metadata_json = task_dir / "metadata.json"
    if not task_yaml.exists() or not prompt_txt.exists() or not metadata_json.exists():
        raise TaskLoadError(f"Task directory missing required files: {task_dir}")
    payload = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    payload["task_dir"] = task_dir
    payload["prompt"] = _read_prompt(prompt_txt)
    payload["metadata"] = json.loads(metadata_json.read_text(encoding="utf-8"))
    try:
        return BenchmarkTask.model_validate(payload)
    except ValidationError as exc:
        raise TaskLoadError(f"Invalid task schema in {task_yaml}: {exc}") from exc


def load_tasks(suite_dir: Path, task_id: str | None = None) -> list[BenchmarkTask]:
    if not suite_dir.exists():
        raise TaskLoadError(f"Task suite not found: {suite_dir}")
    task_dirs = sorted([path for path in suite_dir.iterdir() if path.is_dir() and (path / "task.yaml").exists()])
    tasks = [load_task(path) for path in task_dirs]
    if task_id:
        tasks = [task for task in tasks if task.id == task_id]
        if not tasks:
            raise TaskLoadError(f"Task not found: {task_id}")
    return tasks
