from __future__ import annotations

from pathlib import Path

from villani_code.villani_loop import VillaniLoopConfig, format_villani_summary, run_villani_loop


class SeqRunner:
    def __init__(self, repo: Path, steps: list[dict]) -> None:
        self.repo = repo
        self.steps = steps
        self.idx = 0

    def run_villani_action(self, **_kwargs):
        step = self.steps[min(self.idx, len(self.steps) - 1)] if self.steps else {}
        self.idx += 1
        for rel, content in step.get("writes", []):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {
            "response": {"content": [{"type": "text", "text": step.get("text", "ok")}]},
            "transcript": {"tool_results": []},
            "execution": {
                "intentional_changes": step.get("intentional_changes", []),
                "files_changed": step.get("intentional_changes", []),
                "validation_artifacts": [],
                "runner_failures": [],
            },
        }


def test_python_syntax_failure_blocks_success(tmp_path: Path) -> None:
    runner = SeqRunner(tmp_path, [{"writes": [("dashboard.py", "def oops(:\n    pass\n")], "intentional_changes": ["dashboard.py"]}])
    summary = run_villani_loop(runner, tmp_path, "build a python dashboard script", config=VillaniLoopConfig(max_iterations=2))
    assert summary["beliefs"]["last_validation_passed"] is False
    assert summary["beliefs"]["last_failure_signature"] == "python_compile_failed"
    assert summary["done_reason"] != "objective_validated"


def test_python_runtime_failure_is_recorded(tmp_path: Path) -> None:
    runner = SeqRunner(tmp_path, [{"writes": [("app.py", "raise RuntimeError('boom')\n")], "intentional_changes": ["app.py"]}])
    summary = run_villani_loop(runner, tmp_path, "create a python app tool", config=VillaniLoopConfig(max_iterations=2))
    assert summary["beliefs"]["last_validation_failed"] is True
    assert "python_runtime_failed" in summary["beliefs"]["last_failure_signature"]


def test_html_generation_success_validates(tmp_path: Path) -> None:
    content = "from pathlib import Path\nPath('dashboard.html').write_text('<html><body>ok</body></html>', encoding='utf-8')\n"
    runner = SeqRunner(tmp_path, [{"writes": [("dashboard.py", content)], "intentional_changes": ["dashboard.py"]}])
    summary = run_villani_loop(runner, tmp_path, "generate a dashboard html", config=VillaniLoopConfig(max_iterations=2))
    assert summary["beliefs"]["last_validation_passed"] is True
    assert "dashboard.html" in summary["beliefs"]["last_artifacts_created"]
    assert summary["done_reason"] == "objective_validated"


def test_missing_output_artifact_fails(tmp_path: Path) -> None:
    runner = SeqRunner(tmp_path, [{"writes": [("dashboard.py", "print('done')\n")], "intentional_changes": ["dashboard.py"]}])
    summary = run_villani_loop(runner, tmp_path, "generate dashboard html output", config=VillaniLoopConfig(max_iterations=2))
    assert summary["beliefs"]["last_failure_signature"] == "missing_output_artifact"
    assert summary["done_reason"] != "objective_validated"


def test_loop_detection_with_invalid_deliverable_never_success(tmp_path: Path) -> None:
    runner = SeqRunner(tmp_path, [{"writes": [("dashboard.py", "def oops(:\n")], "intentional_changes": ["dashboard.py"]}] * 5)
    summary = run_villani_loop(runner, tmp_path, "build dashboard html", config=VillaniLoopConfig(max_iterations=4))
    assert summary["done_reason"] in {"validation_failed_repair_exhausted", "loop_without_valid_deliverable"}
    assert summary["done_reason"] != "objective_validated"


def test_summary_reports_validation_proof(tmp_path: Path) -> None:
    content = "from pathlib import Path\nPath('report.html').write_text('<html>ok</html>', encoding='utf-8')\n"
    runner = SeqRunner(tmp_path, [{"writes": [("report.py", content)], "intentional_changes": ["report.py"]}])
    summary = run_villani_loop(runner, tmp_path, "create html report", config=VillaniLoopConfig(max_iterations=2))
    text = format_villani_summary(summary)
    assert "validation_commands:" in text
    assert "validation_passed:" in text
