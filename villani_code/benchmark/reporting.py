from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from villani_code.benchmark.utils import write_csv, write_json


def aggregate_by_agent(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        grouped[row["agent_name"]].append(row)

    aggregates: dict[str, dict[str, float]] = {}
    for agent, rows in grouped.items():
        total = max(len(rows), 1)
        successes = sum(1 for row in rows if row["scorecard"]["task_success"])
        validations = sum(1 for row in rows if row["scorecard"]["validation_success"])
        skips = sum(1 for row in rows if row["scorecard"]["skipped"])
        catastrophic = sum(1 for row in rows if row["scorecard"]["catastrophic_failure"])
        aggregates[agent] = {
            "success_rate": successes / total,
            "validation_pass_rate": validations / total,
            "skip_rate": skips / total,
            "average_composite_score": sum(row["scorecard"]["composite_score"] for row in rows) / total,
            "average_elapsed_time": sum(row["scorecard"]["elapsed_seconds"] for row in rows) / total,
            "catastrophic_failure_rate": catastrophic / total,
            "average_unnecessary_files_touched": sum(row["scorecard"]["unnecessary_files_touched_count"] for row in rows) / total,
        }
    return aggregates


def to_markdown(metadata: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    aggregates = aggregate_by_agent(rows)
    leaderboard = sorted(aggregates.items(), key=lambda item: item[1]["average_composite_score"], reverse=True)
    lines = [
        "# Benchmark Results",
        "",
        f"- Model: `{metadata.get('model')}`",
        f"- Base URL: `{metadata.get('base_url')}`",
        f"- Agents: {', '.join(metadata.get('agents', []))}",
        f"- Run mode: `{metadata.get('run_mode', 'mixed')}`",
        f"- Fairness classification: `{metadata.get('fairness_classification', metadata.get('run_mode', 'mixed'))}`",
        "",
    "## Leaderboard",
        "",
        "| Agent | Success Rate | Validation Pass Rate | Skip Rate | Avg Composite | Avg Time (s) | Catastrophic Failure Rate | Avg Unnecessary Files |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for agent, agg in leaderboard:
        lines.append(
            f"| {agent} | {agg['success_rate']:.2%} | {agg['validation_pass_rate']:.2%} | {agg['skip_rate']:.2%} | {agg['average_composite_score']:.2f} | {agg['average_elapsed_time']:.2f} | {agg['catastrophic_failure_rate']:.2%} | {agg['average_unnecessary_files_touched']:.2f} |"
        )

    fairness_warning = metadata.get("fairness_warning")
    if fairness_warning:
        lines.extend(["", f"> ⚠️ {fairness_warning}"])

    capabilities = metadata.get("agent_capabilities", [])
    if capabilities:
        lines.extend(
            [
                "",
                "## Agent Capabilities",
                "",
                "| Agent | Explicit Base URL | Explicit Model | Noninteractive | Unattended | Fairness | Controllability |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        for row in capabilities:
            lines.append(
                f"| {row.get('agent')} | {row.get('supports_explicit_base_url')} | {row.get('supports_explicit_model')} | {row.get('supports_noninteractive')} | {row.get('supports_unattended')} | {row.get('fairness_classification')} | {row.get('controllability')} |"
            )

    lines.extend(["", "## Per-task Results", "", "| Agent | Task | Success | Validation | Skipped | Composite | Exit Reason | Skip Reason |", "|---|---|---|---|---|---:|---|---|"])
    for row in rows:
        score = row["scorecard"]
        lines.append(
            f"| {row['agent_name']} | {row['task_id']} | {score['task_success']} | {score['validation_success']} | {score['skipped']} | {score['composite_score']:.2f} | {row['exit_reason']} | {row.get('skip_reason') or ''} |"
        )
    return "\n".join(lines)


def persist_reports(output_dir: Path, metadata: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    aggregates = aggregate_by_agent(rows)
    payload = {
        "metadata": metadata,
        "results": rows,
        "aggregate_by_agent": aggregates,
    }
    write_json(output_dir / "benchmark_results.json", payload)
    (output_dir / "benchmark_results.md").write_text(to_markdown(metadata, rows), encoding="utf-8")
    csv_rows = []
    for row in rows:
        score = row["scorecard"]
        csv_rows.append(
            {
                "agent_name": row["agent_name"],
                "task_id": row["task_id"],
                "task_success": score["task_success"],
                "validation_success": score["validation_success"],
                "skipped": score["skipped"],
                "elapsed_seconds": score["elapsed_seconds"],
                "composite_score": score["composite_score"],
            }
        )
    write_csv(
        output_dir / "benchmark_results.csv",
        csv_rows,
        ["agent_name", "task_id", "task_success", "validation_success", "skipped", "elapsed_seconds", "composite_score"],
    )
