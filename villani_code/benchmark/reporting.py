from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from villani_code.benchmark.models import BenchmarkRunResult, BenchmarkSummary
from villani_code.benchmark.stats import bootstrap_delta, wilson_interval


def _safe_mean(values: list[float | int | None]) -> float | None:
    usable = [float(v) for v in values if v is not None]
    return round(mean(usable), 4) if usable else None


def _group_pass(rows: list[BenchmarkRunResult]) -> dict[str, float]:
    total = len(rows)
    success = sum(r.success for r in rows)
    return {"total": total, "successes": success, "pass_rate": round(success / total, 4) if total else 0.0}


def _aggregate(rows: list[BenchmarkRunResult]) -> dict[str, object]:
    return {
        **_group_pass(rows),
        "avg_total_tokens": _safe_mean([r.total_tokens for r in rows]),
        "avg_wall_clock_seconds": _safe_mean([r.wall_clock_seconds for r in rows]),
        "avg_tool_calls_total": _safe_mean([r.tool_calls_total for r in rows]),
        "avg_test_runs": _safe_mean([r.test_runs for r in rows]),
        "avg_patch_attempts": _safe_mean([r.patch_attempts for r in rows]),
        "avg_retries_after_failure": _safe_mean([r.retries_after_failure for r in rows]),
        "first_pass_success_rate": round(sum(1 for r in rows if r.first_pass_success) / total, 4) if (total := len(rows)) else 0.0,
        "recovered_after_failed_attempt_rate": round(sum(1 for r in rows if r.recovered_after_failed_attempt) / total, 4) if total else 0.0,
        "avg_unnecessary_edits": _safe_mean([r.unnecessary_edits for r in rows]),
        "avg_files_touched": _safe_mean([r.files_touched for r in rows]),
        "avg_risky_commands_attempted": _safe_mean([r.risky_commands_attempted for r in rows]),
        "avg_checkpoint_events": _safe_mean([r.checkpoint_events for r in rows]),
        "avg_validation_failures_before_success": _safe_mean([r.validation_failures_before_success for r in rows]),
        "avg_transcript_audit_score": _safe_mean([r.transcript_audit_score for r in rows]),
    }


def write_results(results: list[BenchmarkRunResult], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "results.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(row.model_dump_json())
            handle.write("\n")
    summary = summarize(results)
    (output_dir / "summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "aggregates.json").write_text(aggregate_results(results), encoding="utf-8")
    write_csv(results, output_dir / "results.csv")
    return out


def write_csv(results: list[BenchmarkRunResult], path: Path) -> None:
    fields = [
        "task_id",
        "benchmark_track",
        "benchmark_bucket",
        "task_type",
        "agent_name",
        "model_name",
        "adapter_name",
        "adapter_capability",
        "fairness_classification",
        "telemetry_capability",
        "success",
        "pass_rate",
        "failed",
        "timed_out",
        "runtime_seconds",
        "wall_clock_seconds",
        "total_tokens",
        "estimated_cost",
        "number_of_turns",
        "tool_calls_total",
        "file_reads",
        "file_writes",
        "patch_attempts",
        "test_runs",
        "retries_after_failure",
        "first_pass_success",
        "recovered_after_failed_attempt",
        "files_touched",
        "expected_files_touched_count",
        "actual_files_touched_count",
        "touched_unexpected_files",
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
                    "benchmark_bucket": r.benchmark_bucket,
                    "task_type": r.task_type,
                    "agent_name": r.agent_name,
                    "model_name": r.model_name,
                    "adapter_name": r.adapter_name,
                    "adapter_capability": r.adapter_capability,
                    "fairness_classification": r.fairness_classification.value,
                    "telemetry_capability": r.telemetry_capability,
                    "success": r.success,
                    "pass_rate": r.pass_rate,
                    "failed": r.failed,
                    "timed_out": r.timed_out,
                    "runtime_seconds": r.runtime_seconds,
                    "wall_clock_seconds": r.wall_clock_seconds,
                    "total_tokens": r.total_tokens,
                    "estimated_cost": r.estimated_cost,
                    "number_of_turns": r.number_of_turns,
                    "tool_calls_total": r.tool_calls_total,
                    "file_reads": r.file_reads,
                    "file_writes": r.file_writes,
                    "patch_attempts": r.patch_attempts,
                    "test_runs": r.test_runs,
                    "retries_after_failure": r.retries_after_failure,
                    "first_pass_success": r.first_pass_success,
                    "recovered_after_failed_attempt": r.recovered_after_failed_attempt,
                    "files_touched": r.files_touched,
                    "expected_files_touched_count": r.expected_files_touched_count,
                    "actual_files_touched_count": r.actual_files_touched_count,
                    "touched_unexpected_files": r.touched_unexpected_files,
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


def aggregate_results(results: list[BenchmarkRunResult]) -> str:
    by_task: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    by_task_type: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    by_stressor: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    by_agent: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    by_model: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    by_agent_model: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    by_bucket: dict[str, list[BenchmarkRunResult]] = defaultdict(list)
    for r in results:
        by_task[r.task_id].append(r)
        by_task_type[r.task_type or "unknown"].append(r)
        for stressor in r.runtime_stressors:
            by_stressor[stressor].append(r)
        by_agent[r.agent_name].append(r)
        by_model[r.model_name or "unknown"].append(r)
        by_agent_model[f"{r.agent_name}::{r.model_name or 'unknown'}"].append(r)
        by_bucket[r.benchmark_bucket].append(r)

    import json

    payload = {
        "overall": _aggregate(results),
        "by_task": {k: _aggregate(v) for k, v in sorted(by_task.items())},
        "by_task_type": {k: _aggregate(v) for k, v in sorted(by_task_type.items())},
        "by_runtime_stressor": {k: _aggregate(v) for k, v in sorted(by_stressor.items())},
        "by_agent": {k: _aggregate(v) for k, v in sorted(by_agent.items())},
        "by_model": {k: _aggregate(v) for k, v in sorted(by_model.items())},
        "by_agent_model": {k: _aggregate(v) for k, v in sorted(by_agent_model.items())},
        "by_bucket": {k: _aggregate(v) for k, v in sorted(by_bucket.items())},
    }
    return json.dumps(payload, indent=2)


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
        "aggregates": aggregate_results(results),
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
    import json

    agg = json.loads(d["aggregates"])
    lines = [
        f"tasks={d['summary']['total_tasks']} successes={d['summary']['successes']} success_rate={d['summary']['success_rate']:.2%}",
        "safe_local_track emphasizes trust metrics (restraint, checkpointing, audit clarity).",
        f"ci95=({d['pass_rate_ci_95']['low']:.2%}, {d['pass_rate_ci_95']['high']:.2%}) hidden_after_visible={d['hidden_fail_after_visible_pass_rate']:.2%}",
        "same_model_comparison(agent::model pass_rate first_pass recovered avg_total_tokens avg_wall_s)",
    ]
    for key, row in sorted(agg["by_agent_model"].items()):
        lines.append(
            f"- {key}: {row['pass_rate']:.2%} {row['first_pass_success_rate']:.2%} {row['recovered_after_failed_attempt_rate']:.2%} {row['avg_total_tokens']} {row['avg_wall_clock_seconds']}"
        )
    lines.append("id | bucket | task_type | success | first_pass | recovered | retries | runtime_s")
    for row in results:
        lines.append(
            f"{row.task_id} | {row.benchmark_bucket} | {row.task_type or '-'} | {row.success} | {row.first_pass_success} | {row.recovered_after_failed_attempt} | {row.retries_after_failure} | {row.runtime_seconds:.2f}"
        )
    return "\n".join(lines)


def write_markdown_report(results: list[BenchmarkRunResult], out: Path) -> None:
    d = diagnostics(results)
    import json

    agg = json.loads(d["aggregates"])
    lines = ["# Benchmark Report", "", "## Overall leaderboard", "", "| group | pass_rate | successes/total |", "|---|---:|---:|"]
    for group, rows in [("Agent", agg["by_agent"]), ("Model", agg["by_model"]), ("Agent+Model", agg["by_agent_model"])]:
        for key, val in sorted(rows.items()):
            lines.append(f"| {group}:{key} | {val['pass_rate']:.2%} | {val['successes']}/{val['total']} |")

    lines.extend(["", "## Same-model comparison (small-model focus)", "", "| agent::model | pass_rate | first_pass_success | recovered_after_failed_attempt | avg_total_tokens | avg_wall_clock_seconds |", "|---|---:|---:|---:|---:|---:|"])
    for key, val in sorted(agg["by_agent_model"].items()):
        lines.append(
            f"| {key} | {val['pass_rate']:.2%} | {val['first_pass_success_rate']:.2%} | {val['recovered_after_failed_attempt_rate']:.2%} | {val['avg_total_tokens']} | {val['avg_wall_clock_seconds']} |"
        )

    lines.extend(["", "## Runtime-stressor breakdown", "", "| stressor | pass_rate | avg_retries_after_failure | first_pass_success |", "|---|---:|---:|---:|"])
    for key, val in sorted(agg["by_runtime_stressor"].items()):
        lines.append(f"| {key} | {val['pass_rate']:.2%} | {val['avg_retries_after_failure']} | {val['first_pass_success_rate']:.2%} |")

    lines.extend(["", "## Task-type breakdown", "", "| task_type | pass_rate |", "|---|---:|"])
    for key, val in sorted(agg["by_task_type"].items()):
        lines.append(f"| {key} | {val['pass_rate']:.2%} |")

    lines.extend(["", "## Efficiency summary", "", "| group | avg_total_tokens | avg_wall_clock_seconds | avg_tool_calls_total | avg_test_runs | avg_patch_attempts |", "|---|---:|---:|---:|---:|---:|"])
    overall = agg["overall"]
    lines.append(
        f"| overall | {overall['avg_total_tokens']} | {overall['avg_wall_clock_seconds']} | {overall['avg_tool_calls_total']} | {overall['avg_test_runs']} | {overall['avg_patch_attempts']} |"
    )

    lines.extend(["", "## Task-by-task outcomes", "", "| task | bucket | type | success | retries_after_failure | first_pass_success | recovered_after_failed_attempt |", "|---|---|---|---:|---:|---|---|"])
    for row in results:
        lines.append(
            f"| {row.task_id} | {row.benchmark_bucket} | {row.task_type or '-'} | {row.success} | {row.retries_after_failure} | {row.first_pass_success} | {row.recovered_after_failed_attempt} |"
        )

    out.write_text("\n".join(lines), encoding="utf-8")


def write_html_report(results: list[BenchmarkRunResult], out: Path) -> None:
    d = diagnostics(results)
    rows = "".join(
        f"<tr><td>{r.task_id}</td><td>{r.benchmark_track.value}</td><td>{r.task_family.value}</td><td>{r.benchmark_bucket}</td><td>{r.task_type or '-'}</td><td>{r.success}</td></tr>"
        for r in results
    )
    html = f"""<html><body><h1>Benchmark Report</h1><p>Tasks: {d['summary']['total_tasks']}</p><p>Success: {d['summary']['success_rate']:.2%}</p><table border='1'><tr><th>Task</th><th>Track</th><th>Family</th><th>Bucket</th><th>TaskType</th><th>Success</th></tr>{rows}</table></body></html>"""
    out.write_text(html, encoding="utf-8")
