from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from villani_code.benchmark.models import BenchmarkCategory

BENCHMARK_CATEGORY_VALUES = tuple(category.value for category in BenchmarkCategory)
BENCHMARK_SUITE_DIRS = (
    Path("benchmark_tasks/villani_bench_v1"),
    Path("benchmark_tasks/villani_feature_v1"),
    Path("benchmark_tasks/villani_long_bench_v1"),
    Path("benchmark_tasks/villani_mini_bench_v1"),
)


def iter_task_dirs(suite_dir: Path) -> list[Path]:
    return sorted(path for path in suite_dir.iterdir() if path.is_dir() and (path / "task.yaml").exists())


def load_raw_task_payloads(task_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    task_yaml = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8")) or {}
    metadata_json = json.loads((task_dir / "metadata.json").read_text(encoding="utf-8"))
    return task_yaml, metadata_json


def validate_benchmark_categories(suite_dir: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for task_dir in iter_task_dirs(suite_dir):
        task_yaml, metadata_json = load_raw_task_payloads(task_dir)
        yaml_category = task_yaml.get("benchmark_category")
        metadata_category = metadata_json.get("benchmark_category")
        issue_messages: list[str] = []

        if yaml_category is None:
            issue_messages.append("task.yaml missing benchmark_category")
        elif yaml_category not in BENCHMARK_CATEGORY_VALUES:
            issue_messages.append(
                f"task.yaml benchmark_category={yaml_category!r} must be one of {', '.join(BENCHMARK_CATEGORY_VALUES)}"
            )

        if metadata_category is None:
            issue_messages.append("metadata.json missing benchmark_category")
        elif metadata_category not in BENCHMARK_CATEGORY_VALUES:
            issue_messages.append(
                f"metadata.json benchmark_category={metadata_category!r} must be one of {', '.join(BENCHMARK_CATEGORY_VALUES)}"
            )

        if yaml_category is not None and metadata_category is not None and yaml_category != metadata_category:
            issue_messages.append(
                "task.yaml and metadata.json benchmark_category values must match"
            )

        if issue_messages:
            issues.append(
                {
                    "code": "invalid_benchmark_category",
                    "task": task_dir.name,
                    "message": "; ".join(issue_messages),
                }
            )
    return issues


def benchmark_category_counts(suite_dirs: list[Path] | tuple[Path, ...]) -> dict[str, int]:
    counts = Counter({category: 0 for category in BENCHMARK_CATEGORY_VALUES})
    for suite_dir in suite_dirs:
        for task_dir in iter_task_dirs(suite_dir):
            task_yaml, _ = load_raw_task_payloads(task_dir)
            category = task_yaml.get("benchmark_category")
            if category in BENCHMARK_CATEGORY_VALUES:
                counts[str(category)] += 1
    return dict(sorted(counts.items()))
