from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from rich.console import Console

from villani_code.benchmark.adapters.base import AgentAdapter, AgentAdapterConfig
from villani_code.benchmark.environment import BenchmarkWorkspace
from villani_code.benchmark.logging import BenchmarkLogger
from villani_code.benchmark.runner import BenchmarkRunner


class DummyAdapter(AgentAdapter):
    def run_task(self, task, workspace_repo: Path, artifact_dir: Path):  # pragma: no cover - not used
        raise NotImplementedError


class FakeAdapter:
    def __init__(self, config: AgentAdapterConfig) -> None:
        self.config = config

    def run_task(self, task, workspace_repo: Path, artifact_dir: Path):
        from villani_code.benchmark.adapters.base import AgentRunResult

        (workspace_repo / "marker.txt").write_text("ok", encoding="utf-8")
        return AgentRunResult(
            agent_name=self.config.agent_name,
            task_id=task.id,
            success=True,
            exit_reason="exit:0",
            elapsed_seconds=0.2,
            stdout="done",
            stderr="",
            changed_files=[],
            git_diff="",
            validation_results=[],
            catastrophic_failure=False,
            tokens_input=None,
            tokens_output=None,
            cost_usd=None,
            raw_artifact_dir=str(artifact_dir),
            skipped=False,
            skip_reason=None,
            exit_code=0,
            command=[sys.executable, "-c", "print('hi')"],
        )


class FakeEnvironment:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def create_workspace(self, source_repo: Path, repo_git_ref: str | None = None) -> BenchmarkWorkspace:
        work_repo = self.workspace_root / "repo"
        work_repo.mkdir(parents=True, exist_ok=True)
        return BenchmarkWorkspace(source_repo=source_repo, workspace_root=self.workspace_root, work_repo=work_repo)

    def collect_changed_files(self, work_repo: Path) -> list[str]:
        return ["marker.txt"]

    def collect_git_diff(self, work_repo: Path) -> str:
        return "diff"

    def cleanup(self) -> None:
        return


def _write_task(tasks_dir: Path) -> None:
    (tasks_dir / "task.json").write_text(
        '{"id":"t1","name":"task","instruction":"x","category":"cat","validation_checks":[{"type":"file_contains","path":"marker.txt","substring":"ok"}]}',
        encoding="utf-8",
    )


def test_benchmark_runner_emits_progress_logs(tmp_path: Path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    _write_task(tasks_dir)

    monkeypatch.setattr(BenchmarkRunner, "_build_adapter", staticmethod(lambda agent, config: FakeAdapter(config)))
    sink = StringIO()
    runner = BenchmarkRunner(
        output_dir=tmp_path / "out",
        environment=FakeEnvironment(tmp_path / "workspace"),
        logger=BenchmarkLogger(enabled=True, console=Console(file=sink, force_terminal=False, color_system=None)),
        verbose=True,
        stream_agent_output=False,
    )

    runner.run(
        tasks_dir=tasks_dir,
        task_id=None,
        agents=["villani"],
        repo_path=tmp_path,
        model="m",
        base_url="u",
        api_key=None,
        timeout_seconds=30,
    )

    output = sink.getvalue()
    assert "[benchmark] Loaded 1 tasks and 1 agents" in output
    assert "[benchmark] Validation 1/1 start" in output
    assert "[benchmark] Agent summary: villani" in output
    assert "[benchmark] Final summary:" in output


def test_benchmark_runner_quiet_suppresses_progress_logs(tmp_path: Path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    _write_task(tasks_dir)

    monkeypatch.setattr(BenchmarkRunner, "_build_adapter", staticmethod(lambda agent, config: FakeAdapter(config)))
    sink = StringIO()
    runner = BenchmarkRunner(
        output_dir=tmp_path / "out",
        environment=FakeEnvironment(tmp_path / "workspace"),
        logger=BenchmarkLogger(enabled=False, console=Console(file=sink, force_terminal=False, color_system=None)),
        verbose=False,
        stream_agent_output=False,
    )

    result = runner.run(
        tasks_dir=tasks_dir,
        task_id=None,
        agents=["villani"],
        repo_path=tmp_path,
        model="m",
        base_url="u",
        api_key=None,
        timeout_seconds=30,
    )

    assert sink.getvalue() == ""
    assert Path(result["output_dir"]).exists()


def test_adapter_streaming_keeps_live_output_and_captured_buffers(tmp_path: Path) -> None:
    streamed: list[tuple[str, str, str]] = []
    adapter = DummyAdapter(
        AgentAdapterConfig(
            agent_name="dummy",
            stream_agent_output=True,
            on_output_line=lambda agent, stream, line: streamed.append((agent, stream, line)),
        )
    )
    command = [
        sys.executable,
        "-c",
        "import sys,time; print('out-1'); sys.stdout.flush(); print('err-1', file=sys.stderr); sys.stderr.flush(); time.sleep(0.05); print('out-2'); print('err-2', file=sys.stderr)",
    ]

    result = adapter.run_command(command, tmp_path, timeout_seconds=5)

    assert result.exit_code == 0
    assert "out-1" in result.stdout and "out-2" in result.stdout
    assert "err-1" in result.stderr and "err-2" in result.stderr
    assert ("dummy", "stdout", "out-1") in streamed
    assert ("dummy", "stderr", "err-1") in streamed


def test_skipped_agent_logs_are_emitted(tmp_path: Path, monkeypatch) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    _write_task(tasks_dir)
    monkeypatch.setattr("villani_code.benchmark.adapters.claude_code.command_exists", lambda name: False)

    sink = StringIO()
    runner = BenchmarkRunner(
        output_dir=tmp_path / "out",
        environment=FakeEnvironment(tmp_path / "workspace"),
        logger=BenchmarkLogger(enabled=True, console=Console(file=sink, force_terminal=False, color_system=None)),
        verbose=True,
        stream_agent_output=False,
    )
    runner.run(
        tasks_dir=tasks_dir,
        task_id=None,
        agents=["claude-code"],
        repo_path=tmp_path,
        model="m",
        base_url="u",
        api_key=None,
        timeout_seconds=30,
    )

    assert "Skipping agent 'claude-code' for task 't1'" in sink.getvalue()
