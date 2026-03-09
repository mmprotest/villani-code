from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.execution import ExecutionBudget
from villani_code.state import Runner


class SequenceClient:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    def create_message(self, payload, stream):
        out = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return out


def test_repeated_reads_stop_with_benchmark_reason(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    cfg = BenchmarkRuntimeConfig(enabled=True, task_id="t", task_type="single_file_bugfix", expected_files=["src/app.py"])
    responses = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": str(i), "name": "Read", "input": {"file_path": "src/app.py"}}]}
        for i in range(1, 9)
    ]
    runner = Runner(client=SequenceClient(responses), repo=tmp_path, model="m", stream=False, benchmark_config=cfg)
    out = runner.run("fix", execution_budget=ExecutionBudget(40, 80, 500.0, 40, 40))
    assert out["execution"]["terminated_reason"] == "benchmark_max_reads"


def test_repeated_patch_attempts_stop_early(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    cfg = BenchmarkRuntimeConfig(enabled=True, task_id="t", task_type="single_file_bugfix", expected_files=["src/app.py"], allowlist_paths=["src/"], allowed_support_globs=["src/*.py"])
    patch = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=1\n+x=2\n"
    responses = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": str(i), "name": "Patch", "input": {"unified_diff": patch}}]}
        for i in range(1, 8)
    ]
    runner = Runner(client=SequenceClient(responses), repo=tmp_path, model="m", stream=False, benchmark_config=cfg, auto_accept_edits=True, bypass_permissions=True)
    out = runner.run("fix", execution_budget=ExecutionBudget(40, 80, 500.0, 40, 40))
    assert out["execution"]["terminated_reason"] == "benchmark_repeated_failed_patch"
