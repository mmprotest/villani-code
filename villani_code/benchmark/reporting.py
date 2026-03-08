from __future__ import annotations

import json
from pathlib import Path

from villani_code.benchmark.models import BenchmarkRunResult, BenchmarkSummary


def write_results(results: list[BenchmarkRunResult], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "results.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(row.model_dump_json())
            handle.write("\n")
    summary = summarize(results)
    (output_dir / "summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return out


def load_results(path: Path) -> list[BenchmarkRunResult]:
    rows: list[BenchmarkRunResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(BenchmarkRunResult.model_validate_json(line))
    return rows


def summarize(results: list[BenchmarkRunResult]) -> BenchmarkSummary:
    total = len(results)
    successes = sum(item.success for item in results)
    by_family: dict[str, dict[str, float]] = {}
    for item in results:
        key = item.family.value
        family = by_family.setdefault(key, {"total": 0.0, "successes": 0.0})
        family["total"] += 1
        family["successes"] += item.success
    for family in by_family.values():
        total_family = family["total"]
        family["success_rate"] = round((family["successes"] / total_family) if total_family else 0.0, 4)
    return BenchmarkSummary(
        total_tasks=total,
        successes=successes,
        success_rate=round((successes / total) if total else 0.0, 4),
        by_family=by_family,
    )


def render_summary_table(results: list[BenchmarkRunResult]) -> str:
    summary = summarize(results)
    lines = [
        f"tasks={summary.total_tasks} successes={summary.successes} success_rate={summary.success_rate:.2%}",
        "id | family | success | visible | hidden | runtime_s",
    ]
    for row in results:
        lines.append(
            f"{row.task_id} | {row.family.value} | {row.success} | {row.visible_pass} | {row.hidden_pass} | {row.runtime_seconds:.2f}"
        )
    return "\n".join(lines)
