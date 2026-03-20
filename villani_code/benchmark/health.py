from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from villani_code.benchmark.models import BenchmarkTrack
from villani_code.benchmark.task_loader import TaskLoadError, load_task
from villani_code.benchmark.taxonomy import benchmark_category_counts, validate_benchmark_categories


def _looks_bounded(task) -> bool:
    marker = f"{task.task_type or ''} {task.metadata.task_type or ''} {task.family.value}".lower()
    return any(k in marker for k in ("localize", "adjacent", "forbidden_scope", "bounded", "inspect", "narrow"))


def run_healthcheck(suite_dir: Path) -> dict[str, object]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    tasks = []
    raw_by_task: dict[str, dict[str, object]] = {}
    for task_dir in sorted(path for path in suite_dir.iterdir() if path.is_dir() and (path / "task.yaml").exists()):
        try:
            raw_by_task[task_dir.name] = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8")) or {}
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

    category_issues = validate_benchmark_categories(suite_dir)
    if category_issues:
        offending = ", ".join(issue["task"] for issue in category_issues)
        errors.append(
            {
                "code": "benchmark_category_validation_failed",
                "task": offending,
                "message": "benchmark_category issues found for tasks: "
                + "; ".join(f"{issue['task']} ({issue['message']})" for issue in category_issues),
            }
        )

    for task in tasks:
        raw = raw_by_task.get(task.task_dir.name, {})
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

        raw_hidden_verification = raw.get("hidden_verification")
        raw_hidden_verifier = raw.get("hidden_verifier")
        if raw_hidden_verification is not None and raw_hidden_verifier is not None and raw_hidden_verification != raw_hidden_verifier:
            warnings.append({"code": "hidden_verifier_conflict", "task": task.id, "message": "hidden_verification and hidden_verifier differ; hidden_verification takes precedence"})

        inspect_marker = f"{task.task_type or ''} {task.metadata.task_type or ''} {task.family.value}".lower()
        if task.inspect_only and any(k in inspect_marker for k in ("bugfix", "fix", "refactor", "patch", "repro")):
            warnings.append({"code": "inspect_only_task_suggests_code_modification", "task": task.id, "message": "inspect_only=true but task metadata/family suggests code modification"})
        elif task.inspect_only and not any(k in inspect_marker for k in ("inspect", "read_only", "stop")):
            warnings.append({"code": "inspect_only_metadata_mismatch", "task": task.id, "message": "inspect_only=true but task_type metadata does not look inspect-only"})

        if _looks_bounded(task) and not ((task.allowlist_paths or task.allowed_paths) and task.forbidden_paths):
            warnings.append({"code": "bounded_scope_missing_path_metadata", "task": task.id, "message": "bounded task should define allowlist/allowed paths and forbidden paths"})

    families = Counter(task.family.value for task in tasks)
    difficulties = Counter(task.difficulty.value for task in tasks)

    return {
        "tasks": len(tasks),
        "families": dict(families),
        "difficulties": dict(difficulties),
        "benchmark_categories": benchmark_category_counts((suite_dir,)),
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
