from __future__ import annotations

import json
import subprocess
from pathlib import Path

from villani_code.swebench_live.agents import VillaniAgentRunner
from villani_code.swebench_live.io_utils import run_logged_subprocess, write_predictions
from villani_code.swebench_live.prompting import build_default_prompt
from villani_code.swebench_live.run import run_benchmark
from villani_code.swebench_live.types import AgentConfig, AgentInvocationResult, RunConfig, SwebenchLiveInstance


class FakePreparedInstance:
    def __init__(self, repo_path: str = "/testbed", diff: str = "diff --git a/x b/x\n") -> None:
        self.repo_path = repo_path
        self.diff = diff
        self.commands: list[list[str]] = []
        self.cleaned_up = False
        self.result = AgentInvocationResult(
            exit_code=0,
            timed_out=False,
            duration_seconds=1.0,
            stdout_path=Path("stdout.txt"),
            stderr_path=Path("stderr.txt"),
            command=["python"],
            sanitized_command=["python"],
            error_summary=None,
        )

    def run_process(self, *, command, cwd, env, timeout_seconds, stdout_path, stderr_path):
        self.commands.append(command)
        return self.result

    def capture_diff(self, log_dir: Path) -> str:
        return self.diff

    def cleanup(self) -> None:
        self.cleaned_up = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return None


class FakePreparer:
    def __init__(self, prepared: FakePreparedInstance) -> None:
        self.prepared = prepared

    def prepare(self, instance, config, log_dir):
        return self.prepared


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        dataset="dummy",
        split="verified",
        platform="linux",
        instance_limit=1,
        output_path=tmp_path / "predictions.json",
        logs_path=tmp_path / "logs.jsonl",
        work_dir=tmp_path / "work",
        agent=AgentConfig(provider="openai", model="gpt-test", timeout_seconds=30),
        villani_source_dir=tmp_path,
        install_inside_container=False,
    )


def test_prompt_rendering_includes_problem_statement() -> None:
    prompt = build_default_prompt("Fix the failing parser")
    assert "You are working in /testbed." in prompt
    assert "Issue:\nFix the failing parser" in prompt
    assert prompt.rstrip().endswith("When finished, stop.")


def test_prediction_json_serialization(tmp_path: Path) -> None:
    output = tmp_path / "predictions.json"
    write_predictions({"instance-1": {"model_patch": "diff --git a/x b/x\n"}}, output)
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "instance-1": {"model_patch": "diff --git a/x b/x\n"}
    }


def test_villani_runner_builds_expected_command() -> None:
    runner = VillaniAgentRunner()
    command = runner.build_command(
        prompt="Solve it",
        repo_path="/testbed",
        config=AgentConfig(
            provider="openai",
            model="gpt-test",
            base_url="http://127.0.0.1:1234/v1",
            api_key="secret",
        ),
    )
    assert command == [
        "python",
        "-m",
        "villani_code.cli",
        "run",
        "Solve it",
        "--repo",
        "/testbed",
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


def test_run_benchmark_captures_diff_on_success(tmp_path: Path) -> None:
    prepared = FakePreparedInstance(diff="diff --git a/app.py b/app.py\n")
    config = _config(tmp_path)
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__1", problem_statement="Fix app")],
        preparer=FakePreparer(prepared),
        agent_runner=VillaniAgentRunner(),
    )
    assert predictions == {"repo__1": {"model_patch": "diff --git a/app.py b/app.py\n"}}
    assert logs[0].patch_byte_size == len("diff --git a/app.py b/app.py\n".encode("utf-8"))


def test_run_benchmark_records_empty_patch_when_agent_fails(tmp_path: Path) -> None:
    prepared = FakePreparedInstance()
    prepared.result = AgentInvocationResult(
        exit_code=2,
        timed_out=False,
        duration_seconds=1.0,
        stdout_path=Path("stdout.txt"),
        stderr_path=Path("stderr.txt"),
        command=["python"],
        sanitized_command=["python"],
        error_summary="exit code 2: boom",
    )
    config = _config(tmp_path)
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__2", problem_statement="Fix bug")],
        preparer=FakePreparer(prepared),
        agent_runner=VillaniAgentRunner(),
    )
    assert predictions == {"repo__2": {"model_patch": ""}}
    assert logs[0].error_summary == "exit code 2: boom"


def test_run_benchmark_records_empty_patch_for_empty_diff(tmp_path: Path) -> None:
    prepared = FakePreparedInstance(diff="")
    config = _config(tmp_path)
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__3", problem_statement="No-op")],
        preparer=FakePreparer(prepared),
        agent_runner=VillaniAgentRunner(),
    )
    assert predictions == {"repo__3": {"model_patch": ""}}
    assert logs[0].patch_byte_size == 0


def test_run_benchmark_records_timeout_as_failure(tmp_path: Path) -> None:
    prepared = FakePreparedInstance()
    prepared.result = AgentInvocationResult(
        exit_code=None,
        timed_out=True,
        duration_seconds=30.0,
        stdout_path=Path("stdout.txt"),
        stderr_path=Path("stderr.txt"),
        command=["python"],
        sanitized_command=["python"],
        error_summary="timed out after 30s",
    )
    config = _config(tmp_path)
    predictions, logs = run_benchmark(
        config,
        instances=[SwebenchLiveInstance(instance_id="repo__4", problem_statement="Fix timeout")],
        preparer=FakePreparer(prepared),
        agent_runner=VillaniAgentRunner(),
    )
    assert predictions == {"repo__4": {"model_patch": ""}}
    assert logs[0].timed_out is True
    assert logs[0].error_summary == "timed out after 30s"


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
