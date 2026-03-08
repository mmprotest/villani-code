from __future__ import annotations

import csv
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
    fields = [
        "task_id",
        "benchmark_track",
        "agent_name",
        "adapter_name",
        "adapter_capability",
        "fairness_classification",
        "telemetry_capability",
        "success",
        "failure_reason",
        "runtime_seconds",
        "files_touched",
        "lines_added",
        "lines_deleted",
        "telemetry_quality",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(
                {
                    "task_id": r.task_id,
                    "benchmark_track": r.benchmark_track.value,
                    "agent_name": r.agent_name,
                    "adapter_name": r.adapter_name,
                    "adapter_capability": r.adapter_capability,
                    "fairness_classification": r.fairness_classification.value,
                    "telemetry_capability": r.telemetry_capability,
                    "success": r.success,
                    "failure_reason": r.failure_reason.value if r.failure_reason else "",
                    "runtime_seconds": r.runtime_seconds,
                    "files_touched": r.files_touched,
                    "lines_added": r.lines_added,
                    "lines_deleted": r.lines_deleted,
                    "telemetry_quality": r.telemetry_quality.value,
                }
            )


def load_results(path: Path) -> list[BenchmarkRunResult]:
    rows: list[BenchmarkRunResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(BenchmarkRunResult.model_validate_json(line))
    return rows


def _inc(bucket: dict[str, dict[str, float]], key: str, success: int) -> None:
    stat = bucket.setdefault(key, {"total": 0.0, "successes": 0.0})
    stat["total"] += 1
    stat["successes"] += success


def summarize(results: list[BenchmarkRunResult]) -> BenchmarkSummary:
    total = len(results)
    successes = sum(item.success for item in results)
    by_family: dict[str, dict[str, float]] = {}
    for item in results:
        _inc(by_family, item.task_family.value, item.success)
    for family in by_family.values():
        total_family = family["total"]
        family["success_rate"] = round((family["successes"] / total_family) if total_family else 0.0, 4)
    return BenchmarkSummary(total_tasks=total, successes=successes, success_rate=round((successes / total) if total else 0.0, 4), by_family=by_family)


def diagnostics(results: list[BenchmarkRunResult]) -> dict[str, object]:
    by_track: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
    by_quality: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
    by_fairness: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
    failures = Counter()
    hidden_after_visible = 0
    invalid_repro = 0
    forbidden_edits = 0
    solved_runtimes: list[float] = []
    solved_lines: list[int] = []

    for r in results:
        for bucket, key in [
            (by_track, r.benchmark_track.value),
            (by_family, r.task_family.value),
            (by_quality, r.telemetry_quality.value),
            (by_fairness, r.fairness_classification.value),
        ]:
            bucket[key]["total"] += 1
            bucket[key]["success"] += r.success

        if r.visible_pass and not r.hidden_pass:
            hidden_after_visible += 1
        if r.failure_reason and r.failure_reason.value == "invalid_repro_test":
            invalid_repro += 1
        if r.failure_reason and r.failure_reason.value == "forbidden_edit":
            forbidden_edits += 1
        if r.success:
            solved_runtimes.append(r.runtime_seconds)
            solved_lines.append(r.lines_added + r.lines_deleted)
        if r.failure_reason:
            failures[r.failure_reason.value] += 1
    successes = sum(x.success for x in results)
    ci = wilson_interval(successes, len(results))

    repeats: dict[str, list[int]] = defaultdict(list)
    for r in results:
        repeats[r.task_id].append(r.success)
    instability = {k: {"runs": len(v), "solve_consistency": float(sum(v) / len(v))} for k, v in repeats.items() if len(v) > 1 and len(set(v)) > 1}

    return {
        "summary": summarize(results).model_dump(),
        "by_track": dict(by_track),
        "by_family": dict(by_family),
        "by_telemetry_quality": dict(by_quality),
        "by_fairness_class": dict(by_fairness),
        "hidden_fail_after_visible_pass_rate": hidden_after_visible / len(results) if results else 0.0,
        "invalid_repro_test_rate": invalid_repro / len(results) if results else 0.0,
        "forbidden_edit_rate": forbidden_edits / len(results) if results else 0.0,
        "failure_reason_histogram": dict(failures),
        "solved_runtime_median": median(solved_runtimes) if solved_runtimes else None,
        "solved_lines_changed_median": median(solved_lines) if solved_lines else None,
        "pass_rate_ci_95": {"low": ci[0], "high": ci[1]},
        "instability": instability,
        "small_sample_warning": "Sample size is small; avoid strong significance claims." if len(results) < 10 else None,
    }


def paired_compare(results_a: list[BenchmarkRunResult], results_b: list[BenchmarkRunResult]) -> dict[str, object]:
    a_by_key = {(r.task_id, r.repeat_index): r for r in results_a}
    b_by_key = {(r.task_id, r.repeat_index): r for r in results_b}
    shared = sorted(set(a_by_key) & set(b_by_key))
    a = [a_by_key[t].success for t in shared]
    b = [b_by_key[t].success for t in shared]
    delta, lo, hi = bootstrap_delta(a, b)
    return {
        "shared_tasks": len(shared),
        "a_success": sum(a),
        "b_success": sum(b),
        "delta": delta,
        "delta_ci95": [lo, hi],
        "warning": "small sample" if len(shared) < 10 else None,
    }


def render_summary_table(results: list[BenchmarkRunResult]) -> str:
    d = diagnostics(results)
    lines = [
        f"tasks={d['summary']['total_tasks']} successes={d['summary']['successes']} success_rate={d['summary']['success_rate']:.2%}",
        f"ci95=({d['pass_rate_ci_95']['low']:.2%}, {d['pass_rate_ci_95']['high']:.2%}) hidden_after_visible={d['hidden_fail_after_visible_pass_rate']:.2%}",
        f"core={d['by_track'].get('core', {}).get('success', 0)}/{d['by_track'].get('core', {}).get('total', 0)} feature={d['by_track'].get('feature', {}).get('success', 0)}/{d['by_track'].get('feature', {}).get('total', 0)}",
        "id | track | fairness | telemetry | success | visible | hidden | runtime_s | fail_reason",
    ]
    for row in results:
        lines.append(
            f"{row.task_id} | {row.benchmark_track.value} | {row.fairness_classification.value} | {row.telemetry_quality.value} | {row.success} | {row.visible_pass} | {row.hidden_pass} | {row.runtime_seconds:.2f} | {row.failure_reason.value if row.failure_reason else '-'}"
        )
    return "\n".join(lines)


def write_markdown_report(results: list[BenchmarkRunResult], out: Path) -> None:
    d = diagnostics(results)
    lines = ["# Benchmark Report", "", f"- tasks: {d['summary']['total_tasks']}", f"- success_rate: {d['summary']['success_rate']:.2%}"]
    if d["small_sample_warning"]:
        lines.append(f"- warning: {d['small_sample_warning']}")
    lines.extend(["", "## Track summary (separate)", ""])
    for k, v in sorted(d["by_track"].items()):
        lines.append(f"- {k}: {v['success']}/{v['total']}")
    lines.extend(["", "## Fairness caveats", ""])
    for row in results:
        lines.append(f"- {row.adapter_name}: {row.fairness_classification.value} ({row.fairness_notes})")
    lines.extend(["", "## Telemetry quality", ""])
    for k, v in sorted(d["by_telemetry_quality"].items()):
        lines.append(f"- {k}: {v['success']}/{v['total']}")
    out.write_text("\n".join(dict.fromkeys(lines)), encoding="utf-8")


def write_html_report(results: list[BenchmarkRunResult], out: Path) -> None:
    d = diagnostics(results)
    rows = "".join(
        f"<tr><td>{r.task_id}</td><td>{r.benchmark_track.value}</td><td>{r.task_family.value}</td><td>{r.fairness_classification.value}</td><td>{r.telemetry_quality.value}</td><td>{r.success}</td></tr>"
        for r in results
    )
    html = f"""<html><body><h1>Benchmark Report</h1><p>Tasks: {d['summary']['total_tasks']}</p><p>Success: {d['summary']['success_rate']:.2%}</p><table border='1'><tr><th>Task</th><th>Track</th><th>Family</th><th>Fairness</th><th>Telemetry</th><th>Success</th></tr>{rows}</table></body></html>"""
    out.write_text(html, encoding="utf-8")
