from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from villani_code.swebench_live.agents import VillaniAgentRunner
from villani_code.swebench_live.io_utils import run_logged_subprocess, write_predictions
from villani_code.swebench_live.prompting import build_default_prompt
from villani_code.swebench_live.run import BLOCKED_TASK_ENV_INSTALL_SNIPPETS, PreparedInstance, run_benchmark
from villani_code.swebench_live.types import AgentConfig, AgentInvocationResult, RunConfig, SwebenchLiveInstance, WorkspaceMapping


class FakePreparedInstance:
    def __init__(
        self,
        *,
        host_repo_path: Path,
        task_repo_path: str = "/testbed",
        diff: str = "diff --git a/x b/x\n",
    ) -> None:
        self.workspace = WorkspaceMapping(host_repo_path=host_repo_path, task_repo_path=task_repo_path)
        self.diff = diff
        self.commands: list[list[str]] = []
        self.synced_to_task = False
        self.cleaned_up = False
        self.result = AgentInvocationResult(
            exit_code=0,
            timed_out=False,
            duration_seconds=1.0,
            stdout_path=host_repo_path / "stdout.txt",
            stderr_path=host_repo_path / "stderr.txt",
            command=["python"],
            sanitized_command=["python"],
            error_summary=None,
        )

    def sync_repo_to_task(self, log_dir: Path) -> None:
        self.synced_to_task = True

    def capture_diff(self, log_dir: Path) -> str:
        return self.diff

    def cleanup(self) -> None:
        self.cleaned_up = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return None




class FakeAgentRunner:
    def run(self, prepared_instance, prompt, config, log_dir):
        return prepared_instance.result

class FakePreparer:
    def __init__(self, prepared: FakePreparedInstance) -> None:
        self.prepared = prepared

    def prepare(self, instance, config, log_dir):
        return self.prepared


def _config(tmp_path: Path, **agent_overrides: object) -> RunConfig:
    agent = AgentConfig(
        provider="openai",
        model="gpt-test",
        timeout_seconds=30,
        **agent_overrides,
    )
    return RunConfig(
        dataset="dummy",
        split="verified",
        platform="linux",
        instance_limit=1,
        output_path=tmp_path / "predictions.json",
        logs_path=tmp_path / "logs.jsonl",
        work_dir=tmp_path / "work",
        agent=agent,
    )


def test_prompt_rendering_includes_mapping_note_when_host_path_differs() -> None:
    prompt = build_default_prompt("Fix the failing parser", accessible_repo_path="/tmp/instance/workspace_repo")
    assert "You are working in /testbed." in prompt
    assert "Issue:\nFix the failing parser" in prompt
    assert "mounted at /tmp/instance/workspace_repo" in prompt


def test_prediction_json_serialization(tmp_path: Path) -> None:
    output = tmp_path / "predictions.json"
    write_predictions({"instance-1": {"model_patch": "diff --git a/x b/x\n"}}, output)
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "instance-1": {"model_patch": "diff --git a/x b/x\n"}
    }


def test_villani_runner_builds_expected_command_with_runner_python(tmp_path: Path) -> None:
    runner = VillaniAgentRunner()
    fake_python = tmp_path / "python"
    fake_python.write_text("", encoding="utf-8")
    command = runner.build_command(
        prompt="Solve it",
        repo_path=tmp_path / "repo",
        config=AgentConfig(
            provider="openai",
            model="gpt-test",
            base_url="http://127.0.0.1:1234/v1",
            api_key="secret",
            runner_python=str(fake_python),
        ),
    )
    assert command == [
        str(fake_python),
        "-m",
        "villani_code.cli",
        "run",
        "Solve it",
        "--repo",
        str(tmp_path / "repo"),
        "--provider",
        "openai",
        "--model",
        "gpt-test",
        "--no-stream",
        "--base-url",
        "http://127.0.0.1:1234/v1",
        "--api-key",
        "secret",
    ]


def test_villani_runner_builds_expected_command_with_runner_prefix() -> None:
    runner = VillaniAgentRunner()
    command = runner.build_command(
        prompt="Solve it",
        repo_path=Path("/tmp/repo"),
        config=AgentConfig(
            provider="openai",
            model="gpt-test",
            runner_command_prefix=("python", "-m", "villani_code.cli"),
        ),
    )
    assert command[:4] == ["python", "-m", "villani_code.cli", "run"]
    assert command[4] == "Solve it"
    assert command[5:7] == ["--repo", "/tmp/repo"]


def test_missing_runner_executable_raises_clear_error(tmp_path: Path) -> None:
    runner = VillaniAgentRunner()
    with pytest.raises(RuntimeError, match="Runner executable does not exist"):
        runner.build_command(
            prompt="Solve it",
            repo_path=tmp_path / "repo",
            config=AgentConfig(
                provider="openai",
                model="gpt-test",
                runner_python=str(tmp_path / "missing-python"),
            ),
        )


def test_prepared_instance_blocks_task_env_install_commands(tmp_path: Path) -> None:
    prepared = PreparedInstance(
        instance_id="x",
        container_name="c",
        platform="linux",
        task_repo_path="/testbed",
        host_repo_path=tmp_path / "repo",
        log_dir=tmp_path,
    )
    for snippet in BLOCKED_TASK_ENV_INSTALL_SNIPPETS:
        with pytest.raises(RuntimeError, match="Refusing to install or run villani-code"):
            prepared._assert_task_command_is_safe(snippet.split())


def test_run_benchmark_captures_diff_after_sync(tmp_path: Path) -> None:
    host_repo = tmp_path / "host_repo"
    host_repo.mkdir()
    prepared = FakePreparedInstance(host_repo_path=host_repo, diff="diff --git a/app.py b/app.py\n")
    config = _config(tmp_path, runner_command_prefix=("python", "-m", "villani_code.cli"))
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__1", problem_statement="Fix app")],
        preparer=FakePreparer(prepared),
        agent_runner=FakeAgentRunner(),
    )
    assert predictions == {"repo__1": {"model_patch": "diff --git a/app.py b/app.py\n"}}
    assert prepared.synced_to_task is True
    assert logs[0].patch_byte_size == len("diff --git a/app.py b/app.py\n".encode("utf-8"))


def test_run_benchmark_records_empty_patch_when_agent_fails(tmp_path: Path) -> None:
    host_repo = tmp_path / "host_repo"
    host_repo.mkdir()
    prepared = FakePreparedInstance(host_repo_path=host_repo)
    prepared.result = AgentInvocationResult(
        exit_code=2,
        timed_out=False,
        duration_seconds=1.0,
        stdout_path=host_repo / "stdout.txt",
        stderr_path=host_repo / "stderr.txt",
        command=["python"],
        sanitized_command=["python"],
        error_summary="exit code 2: boom",
    )
    config = _config(tmp_path, runner_command_prefix=("python", "-m", "villani_code.cli"))
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__2", problem_statement="Fix bug")],
        preparer=FakePreparer(prepared),
        agent_runner=FakeAgentRunner(),
    )
    assert predictions == {"repo__2": {"model_patch": ""}}
    assert logs[0].error_summary == "exit code 2: boom"
    assert prepared.synced_to_task is False


def test_run_benchmark_records_empty_patch_for_empty_diff(tmp_path: Path) -> None:
    host_repo = tmp_path / "host_repo"
    host_repo.mkdir()
    prepared = FakePreparedInstance(host_repo_path=host_repo, diff="")
    config = _config(tmp_path, runner_command_prefix=("python", "-m", "villani_code.cli"))
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__3", problem_statement="No-op")],
        preparer=FakePreparer(prepared),
        agent_runner=FakeAgentRunner(),
    )
    assert predictions == {"repo__3": {"model_patch": ""}}
    assert logs[0].patch_byte_size == 0


def test_run_benchmark_records_timeout_as_failure(tmp_path: Path) -> None:
    host_repo = tmp_path / "host_repo"
    host_repo.mkdir()
    prepared = FakePreparedInstance(host_repo_path=host_repo)
    prepared.result = AgentInvocationResult(
        exit_code=None,
        timed_out=True,
        duration_seconds=30.0,
        stdout_path=host_repo / "stdout.txt",
        stderr_path=host_repo / "stderr.txt",
        command=["python"],
        sanitized_command=["python"],
        error_summary="timed out after 30s",
    )
    config = _config(tmp_path, runner_command_prefix=("python", "-m", "villani_code.cli"))
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__4", problem_statement="Fix timeout")],
        preparer=FakePreparer(prepared),
        agent_runner=FakeAgentRunner(),
    )
    assert predictions == {"repo__4": {"model_patch": ""}}
    assert logs[0].timed_out is True
    assert logs[0].error_summary == "timed out after 30s"


def test_external_runner_path_mismatch_is_reported(tmp_path: Path) -> None:
    prepared = FakePreparedInstance(host_repo_path=tmp_path / "missing-host-repo")
    config = _config(tmp_path, runner_command_prefix=("python", "-m", "villani_code.cli"))
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__5", problem_statement="Fix mismatch")],
        preparer=FakePreparer(prepared),
        agent_runner=VillaniAgentRunner(),
    )
    assert predictions == {"repo__5": {"model_patch": ""}}
    assert "cannot access benchmark repo path" in str(logs[0].error_summary)


def test_logged_subprocess_captures_timeout(tmp_path: Path) -> None:
    stdout_path = tmp_path / "stdout.txt"
    stderr_path = tmp_path / "stderr.txt"

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"], output="partial", stderr="late")

    import villani_code.swebench_live.io_utils as io_utils

    original = io_utils.subprocess.run
    io_utils.subprocess.run = fake_run
    try:
        result = run_logged_subprocess(
            ["python", "-c", "print('x')"],
            cwd=None,
            env=None,
            timeout_seconds=1,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    finally:
        io_utils.subprocess.run = original

    assert result.timed_out is True
    assert result.exit_code is None
    assert stdout_path.read_text(encoding="utf-8") == "partial"
    assert "[timeout]" in stderr_path.read_text(encoding="utf-8")
