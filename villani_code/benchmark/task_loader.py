from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from villani_code.benchmark.models import BenchmarkTask, BenchmarkTrack, TaskMetadata


class TaskLoadError(RuntimeError):
    pass


def _read_prompt(prompt_file: Path) -> str:
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if "\n" in prompt:
        raise TaskLoadError(f"{prompt_file} must contain exactly one short instruction")
    if not prompt:
        raise TaskLoadError(f"{prompt_file} is empty")
    return prompt


def _checksum_files(task_dir: Path, rel_paths: list[str]) -> str:
    hasher = hashlib.sha256()
    for rel in rel_paths:
        p = task_dir / rel
        if p.exists():
            hasher.update(rel.encode("utf-8"))
            hasher.update(p.read_bytes())
    return hasher.hexdigest()[:16]


def _resolve_track(task_dir: Path, payload: dict[str, object], metadata_raw: dict[str, object]) -> BenchmarkTrack:
    if "benchmark_track" in payload:
        return BenchmarkTrack(str(payload["benchmark_track"]))
    if "benchmark_track" in metadata_raw:
        return BenchmarkTrack(str(metadata_raw["benchmark_track"]))
    raise TaskLoadError(f"Task {task_dir.name} missing required benchmark_track metadata (core|feature)")


def load_task(task_dir: Path) -> BenchmarkTask:
    task_yaml = task_dir / "task.yaml"
    prompt_txt = task_dir / "prompt.txt"
    metadata_json = task_dir / "metadata.json"
    if not task_yaml.exists() or not prompt_txt.exists() or not metadata_json.exists():
        raise TaskLoadError(f"Task directory missing required files: {task_dir}")

    payload = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
    metadata_raw = json.loads(metadata_json.read_text(encoding="utf-8"))

    payload["task_dir"] = task_dir
    payload["prompt"] = _read_prompt(prompt_txt)
    payload["benchmark_track"] = _resolve_track(task_dir, payload, metadata_raw)
    payload.setdefault("source_type", metadata_raw.get("source_type", "curated"))
    payload.setdefault("tags", metadata_raw.get("tags", []))
    payload.setdefault("task_version", str(metadata_raw.get("task_version", payload.get("task_version", "1.0"))))
    payload.setdefault("expected_patch_size_band", metadata_raw.get("expected_patch_size_band", "small"))
    payload.setdefault("task_variant_family", metadata_raw.get("task_variant_family"))
    payload.setdefault("variant_id", metadata_raw.get("variant_id"))
    payload.setdefault("forbidden_paths", metadata_raw.get("forbidden_paths", [".git/", "hidden_checks/"]))
    payload.setdefault("env_allowlist", metadata_raw.get("env_allowlist", []))
    payload["metadata"] = TaskMetadata.model_validate(metadata_raw)

    payload["task_checksum"] = _checksum_files(task_dir, ["task.yaml", "prompt.txt", "metadata.json"])
    try:
        return BenchmarkTask.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        raise TaskLoadError(f"Invalid task schema in {task_yaml}: {exc}") from exc


def load_tasks(
    suite_dir: Path,
    task_id: str | None = None,
    family: str | None = None,
    difficulty: str | None = None,
    tag: str | None = None,
    source_type: str | None = None,
    track: str | None = None,
    language: str | None = None,
) -> list[BenchmarkTask]:
    if not suite_dir.exists():
        raise TaskLoadError(f"Task suite not found: {suite_dir}")
    task_dirs = sorted(path for path in suite_dir.iterdir() if path.is_dir() and (path / "task.yaml").exists())
    tasks = [load_task(path) for path in task_dirs]
    if task_id:
        task_ids = {part.strip() for part in task_id.split(",") if part.strip()}
        tasks = [task for task in tasks if task.id in task_ids]
    if family:
        tasks = [task for task in tasks if task.family.value == family]
    if difficulty:
        tasks = [task for task in tasks if task.difficulty.value == difficulty]
    if tag:
        tasks = [task for task in tasks if tag in task.tags]
    if source_type:
        tasks = [task for task in tasks if task.source_type.value == source_type]
    if track:
        tasks = [task for task in tasks if task.benchmark_track.value == track]
    if language:
        tasks = [task for task in tasks if task.language == language]
    if task_id and not tasks:
        raise TaskLoadError(f"Task not found: {task_id}")
    return tasks
