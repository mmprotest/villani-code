from __future__ import annotations

from collections import Counter
from pathlib import Path

from villani_code.benchmark.task_loader import load_tasks


def run_healthcheck(suite_dir: Path) -> dict[str, object]:
    tasks = load_tasks(suite_dir)
    checksums = Counter(task.task_checksum for task in tasks)
    duplicate_checksums = [k for k, v in checksums.items() if k and v > 1]
    families = Counter(task.family.value for task in tasks)
    difficulties = Counter(task.difficulty.value for task in tasks)
    stale = [task.id for task in tasks if task.task_version.startswith("0.")]
    return {
        "tasks": len(tasks),
        "families": dict(families),
        "difficulties": dict(difficulties),
        "duplicate_checksums": duplicate_checksums,
        "stale_tasks": stale,
        "ok": not duplicate_checksums,
    }
