from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from villani_code.benchmark.models import BenchmarkRunResult, BenchmarkSummary
from villani_code.benchmark.stats import bootstrap_delta, wilson_interval


def write_results(results: list[BenchmarkRunResult], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "results.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(row.model_dump_json())
            handle.write("\n")
    summary = summarize(results)
    (output_dir / "summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    write_csv(results, output_dir / "results.csv")
    return out


def write_csv(results: list[BenchmarkRunResult], path: Path) -> None:
    fields = ["task_id", "agent_name", "success", "failure_reason", "runtime_seconds", "files_touched", "lines_added", "lines_deleted", "telemetry_quality"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow({
                "task_id": r.task_id,
                "agent_name": r.agent_name,
                "success": r.success,
                "failure_reason": r.failure_reason.value if r.failure_reason else "",
                "runtime_seconds": r.runtime_seconds,
                "files_touched": r.files_touched,
                "lines_added": r.lines_added,
                "lines_deleted": r.lines_deleted,
                "telemetry_quality": r.telemetry_quality.value,
            })


def load_results(path: Path) -> list[BenchmarkRunResult]:
    rows: list[BenchmarkRunResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(BenchmarkRunResult.model_validate_json(line))
    return rows


def summarize(results: list[BenchmarkRunResult]) -> BenchmarkSummary:
    total = len(results)
    successes = sum(item.success for item in results)
    by_family: dict[str, dict[str, float]] = {}
    for item in results:
        key = item.task_family.value
        family = by_family.setdefault(key, {"total": 0.0, "successes": 0.0})
        family["total"] += 1
        family["successes"] += item.success
    for family in by_family.values():
        total_family = family["total"]
        family["success_rate"] = round((family["successes"] / total_family) if total_family else 0.0, 4)
    return BenchmarkSummary(total_tasks=total, successes=successes, success_rate=round((successes / total) if total else 0.0, 4), by_family=by_family)


def diagnostics(results: list[BenchmarkRunResult]) -> dict[str, object]:
    by_agent = defaultdict(lambda: {"total": 0, "success": 0})
    by_diff = defaultdict(lambda: {"total": 0, "success": 0})
    by_lang = defaultdict(lambda: {"total": 0, "success": 0})
    by_quality = defaultdict(lambda: {"total": 0, "success": 0})
    failures = Counter()
    hidden_after_visible = 0
    solved_runtimes: list[float] = []
    solved_files: list[int] = []
    solved_lines: list[int] = []
    for r in results:
        for bucket, key in [(by_agent, r.agent_name), (by_diff, r.task_difficulty.value), (by_lang, r.task_language), (by_quality, r.telemetry_quality.value)]:
            bucket[key]["total"] += 1
            bucket[key]["success"] += r.success
        if r.visible_pass and not r.hidden_pass:
            hidden_after_visible += 1
        if r.success:
            solved_runtimes.append(r.runtime_seconds)
            solved_files.append(r.files_touched)
            solved_lines.append(r.lines_added + r.lines_deleted)
        if r.failure_reason:
            failures[r.failure_reason.value] += 1
    successes = sum(x.success for x in results)
    ci = wilson_interval(successes, len(results))
    return {
        "summary": summarize(results).model_dump(),
        "by_agent": dict(by_agent),
        "by_difficulty": dict(by_diff),
        "by_language": dict(by_lang),
        "by_telemetry_quality": dict(by_quality),
        "hidden_fail_after_visible_pass_rate": hidden_after_visible / len(results) if results else 0.0,
        "timeout_rate": sum(1 for r in results if r.timeout) / len(results) if results else 0.0,
        "forbidden_edit_rate": sum(1 for r in results if r.failure_reason and r.failure_reason.value == "forbidden_edit") / len(results) if results else 0.0,
        "failure_reason_histogram": dict(failures),
        "solved_runtime_median": median(solved_runtimes) if solved_runtimes else None,
        "solved_files_touched_median": median(solved_files) if solved_files else None,
        "solved_lines_changed_median": median(solved_lines) if solved_lines else None,
        "pass_rate_ci_95": {"low": ci[0], "high": ci[1]},
    }


def paired_compare(results_a: list[BenchmarkRunResult], results_b: list[BenchmarkRunResult]) -> dict[str, object]:
    a_by_task = {r.task_id: r for r in results_a}
    b_by_task = {r.task_id: r for r in results_b}
    shared = sorted(set(a_by_task) & set(b_by_task))
    a = [a_by_task[t].success for t in shared]
    b = [b_by_task[t].success for t in shared]
    delta, lo, hi = bootstrap_delta(a, b)
    return {"shared_tasks": len(shared), "a_success": sum(a), "b_success": sum(b), "delta": delta, "delta_ci95": [lo, hi]}


def render_summary_table(results: list[BenchmarkRunResult]) -> str:
    d = diagnostics(results)
    lines = [
        f"tasks={d['summary']['total_tasks']} successes={d['summary']['successes']} success_rate={d['summary']['success_rate']:.2%}",
        f"ci95=({d['pass_rate_ci_95']['low']:.2%}, {d['pass_rate_ci_95']['high']:.2%}) hidden_after_visible={d['hidden_fail_after_visible_pass_rate']:.2%}",
        "id | family | success | visible | hidden | runtime_s | fail_reason",
    ]
    for row in results:
        lines.append(f"{row.task_id} | {row.task_family.value} | {row.success} | {row.visible_pass} | {row.hidden_pass} | {row.runtime_seconds:.2f} | {row.failure_reason.value if row.failure_reason else '-'}")
    return "\n".join(lines)


def write_markdown_report(results: list[BenchmarkRunResult], out: Path) -> None:
    d = diagnostics(results)
    lines = ["# Benchmark Report", "", f"- tasks: {d['summary']['total_tasks']}", f"- success_rate: {d['summary']['success_rate']:.2%}", "", "## Failure histogram", ""]
    for k, v in sorted(d["failure_reason_histogram"].items()):
        lines.append(f"- {k}: {v}")
    out.write_text("\n".join(lines), encoding="utf-8")
