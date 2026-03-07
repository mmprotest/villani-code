import json
from pathlib import Path

from villani_code.benchmark.reporting import aggregate_by_agent, persist_reports


def _row(agent: str, task: str, success: bool, skipped: bool, provenance: str | None = None) -> dict:
    return {
        "agent_name": agent,
        "task_id": task,
        "exit_reason": "ok" if success else "failed",
        "failure_provenance": provenance,
        "scorecard": {
            "task_success": success,
            "validation_success": success,
            "elapsed_seconds": 1.0,
            "changed_files_count": 1,
            "unnecessary_files_touched_count": 0,
            "forbidden_files_touched_count": 0,
            "catastrophic_failure": False,
            "retry_count": 0,
            "tokens_input": None,
            "tokens_output": None,
            "cost_usd": None,
            "skipped": skipped,
            "composite_score": 99.9 if success else 0.0,
            "failure_provenance": provenance,
        },
    }


def test_reporting_outputs_json_markdown_and_csv(tmp_path: Path) -> None:
    rows = [_row("villani", "task1", True, False), _row("claude-code", "task1", False, True, "agent_failure")]
    persist_reports(
        tmp_path,
        {
            "model": "m",
            "base_url": "u",
            "agents": ["villani", "claude-code"],
            "run_mode": "mixed",
            "pack_name": "internal_regressions",
            "pack_classification": "internal_regression",
            "pack_description": "internal",
            "comparison_suitability": "internal_only",
            "fairness_classification": "mixed",
            "fairness_warning": "Fairness warning: mixed-mode comparison.",
            "interpretation_status": "internal_only",
            "interpretation_warning": "This run is internal-only and intended for regression tracking.",
            "agent_capabilities": [
                {
                    "agent": "villani",
                    "supports_explicit_base_url": True,
                    "supports_explicit_model": True,
                    "supports_noninteractive": True,
                    "supports_unattended": True,
                    "fairness_classification": "same-backend",
                    "controllability": "Explicit base_url and model are configurable.",
                }
            ],
        },
        rows,
    )

    assert (tmp_path / "benchmark_results.json").exists()
    assert (tmp_path / "benchmark_results.md").exists()
    assert (tmp_path / "benchmark_results.csv").exists()

    payload = json.loads((tmp_path / "benchmark_results.json").read_text(encoding="utf-8"))
    assert "aggregate_by_agent" in payload
    assert "claude-code" in payload["aggregate_by_agent"]
    markdown = (tmp_path / "benchmark_results.md").read_text(encoding="utf-8")
    assert "Fairness warning" in markdown
    assert "Agent Capabilities" in markdown
    assert "Interpretation status" in markdown
    assert "internal-only" in markdown
    assert "Failure Provenance Summary" in markdown


def test_aggregate_includes_skip_rate_and_provenance_counts() -> None:
    rows = [
        _row("copilot-cli", "a", False, True),
        _row("copilot-cli", "b", False, False, "environment_failure"),
        _row("copilot-cli", "c", True, False),
    ]
    agg = aggregate_by_agent(rows)
    assert agg["copilot-cli"]["skip_rate"] == 1 / 3
    assert agg["copilot-cli"]["environment_failure_count"] == 1
    assert agg["copilot-cli"]["skip_count"] == 1
