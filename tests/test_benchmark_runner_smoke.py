from dataclasses import replace
from pathlib import Path

from villani_code.benchmark.adapters.base import AgentAdapterConfig, AgentRunResult, ValidationResult
from villani_code.benchmark.runner import BenchmarkRunner


class FakeAdapter:
    def __init__(self, config: AgentAdapterConfig) -> None:
        self.config = config

    def run_task(self, task, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        (workspace_repo / "marker.txt").write_text("ok", encoding="utf-8")
        return AgentRunResult(
            agent_name=self.config.agent_name,
            task_id=task.id,
            success=True,
            exit_reason="exit:0",
            elapsed_seconds=0.1,
            stdout="done",
            stderr="",
            changed_files=[],
            git_diff="",
            validation_results=[],
            catastrophic_failure=False,
            tokens_input=1,
            tokens_output=1,
            cost_usd=0.01,
            raw_artifact_dir=str(artifact_dir),
            skipped=False,
            skip_reason=None,
        )


def test_runner_smoke_with_fake_adapter(monkeypatch, tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "task.json").write_text(
        '{"id":"t1","name":"task","instruction":"x","category":"cat","validation_checks":[{"type":"file_contains","path":"marker.txt","substring":"ok"}]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(BenchmarkRunner, "_build_adapter", staticmethod(lambda agent, config: FakeAdapter(config)))
    runner = BenchmarkRunner(output_dir=tmp_path / "out")
    result = runner.run(
        tasks_dir=tasks_dir,
        task_id=None,
        agents=["villani"],
        repo_path=Path("."),
        model="m",
        base_url="u",
        api_key=None,
        timeout_seconds=30,
    )
    assert result["results"]
    assert result["results"][0]["scorecard"]["task_success"] is True
