from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from villani_code.benchmark.models import BenchmarkTrack
from villani_code.benchmark.task_loader import TaskLoadError, load_task, load_tasks


def run_healthcheck(suite_dir: Path) -> dict[str, object]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    tasks = []
    for task_dir in sorted(path for path in suite_dir.iterdir() if path.is_dir() and (path / "task.yaml").exists()):
        try:
            tasks.append(load_task(task_dir))
        except TaskLoadError as exc:
            errors.append({"code": "invalid_task", "task": task_dir.name, "message": str(exc)})

    checksums = Counter(task.task_checksum for task in tasks if task.task_checksum)
    duplicate_checksums = [k for k, v in checksums.items() if v > 1]
    for checksum in duplicate_checksums:
        errors.append({"code": "duplicate_task_checksum", "task": checksum, "message": "Multiple tasks share checksum"})

    ids = Counter(task.id for task in tasks)
    for task_id, count in ids.items():
        if count > 1:
            errors.append({"code": "duplicate_task_id", "task": task_id, "message": "Duplicate task id"})

    for task in tasks:
        if task.benchmark_track not in {BenchmarkTrack.CORE, BenchmarkTrack.FEATURE}:
            errors.append({"code": "invalid_track", "task": task.id, "message": "benchmark_track must be core|feature"})
        if not task.visible_verification:
            errors.append({"code": "missing_visible_checks", "task": task.id, "message": "visible_verification is empty"})
        if not task.hidden_verification:
            errors.append({"code": "missing_hidden_checks", "task": task.id, "message": "hidden_verification is empty"})
        if not task.allowlist_paths:
            errors.append({"code": "broken_allowlist", "task": task.id, "message": "allowlist_paths empty"})
        if not task.metadata.expected_files:
            warnings.append({"code": "missing_expected_files", "task": task.id, "message": "metadata.expected_files is empty"})
        if not task.metadata.primary_skill:
            warnings.append({"code": "missing_primary_skill", "task": task.id, "message": "metadata.primary_skill is empty"})
        if task.task_version.startswith("0."):
            warnings.append({"code": "stale_task_version", "task": task.id, "message": "task_version starts with 0."})
        if task.metadata.benchmark_bucket not in {"baseline", "runtime_stressing"}:
            errors.append({"code": "invalid_benchmark_bucket", "task": task.id, "message": "metadata.benchmark_bucket must be baseline|runtime_stressing"})
        if not task.metadata.task_type:
            warnings.append({"code": "missing_task_type", "task": task.id, "message": "metadata.task_type is empty"})
        if not task.metadata.runtime_stressors:
            warnings.append({"code": "missing_runtime_stressors", "task": task.id, "message": "metadata.runtime_stressors is empty"})
        leaked = [p for p in (task.task_dir / "repo").rglob("*") if p.is_file() and "hidden_checks" in p.as_posix()]
        if leaked:
            errors.append({"code": "hidden_asset_leak", "task": task.id, "message": "hidden_checks assets leaked into repo"})

    families = Counter(task.family.value for task in tasks)
    difficulties = Counter(task.difficulty.value for task in tasks)

    return {
        "tasks": len(tasks),
        "families": dict(families),
        "difficulties": dict(difficulties),
        "errors": errors,
        "warnings": warnings,
        "duplicate_checksums": duplicate_checksums,
        "ok": not errors,
    }


def validate_tasks(suite_dir: Path) -> dict[str, object]:
    health = run_healthcheck(suite_dir)
    return {
        "valid": health["tasks"] if health["ok"] else 0,
        "suite": str(suite_dir),
        "error_count": len(health["errors"]),
        "warning_count": len(health["warnings"]),
        "ok": health["ok"],
    }
