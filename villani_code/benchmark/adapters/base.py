from __future__ import annotations

import os
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from villani_code.benchmark.models import BenchmarkTask
from villani_code.benchmark.fairness import AdapterCapabilities


@dataclass(slots=True)
class AgentAdapterConfig:
    agent_name: str
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int = 300
    unsafe: bool = False
    max_tokens: int | None = None
    thinking: str | None = None
    stream: bool = False
    provider: str | None = None
    extra_args: list[str] = field(default_factory=list)
    stream_agent_output: bool = True
    on_output_line: Callable[[str, str, str], None] | None = None
    on_command_start: Callable[[str, list[str]], None] | None = None


@dataclass(slots=True)
class ValidationResult:
    check_type: str
    success: bool
    details: str
    exit_code: int | None = None
    check_index: int = 0
    failure_provenance: str | None = None


@dataclass(slots=True)
class AgentRunResult:
    agent_name: str
    task_id: str
    success: bool
    exit_reason: str
    elapsed_seconds: float
    stdout: str
    stderr: str
    changed_files: list[str]
    git_diff: str
    validation_results: list[ValidationResult]
    catastrophic_failure: bool
    tokens_input: int | None
    tokens_output: int | None
    cost_usd: float | None
    raw_artifact_dir: str
    skipped: bool
    skip_reason: str | None
    retry_count: int = 0
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CommandExecutionResult:
    exit_code: int | None
    exit_reason: str
    stdout: str
    stderr: str
    elapsed_seconds: float
    catastrophic_failure: bool


class AgentAdapter(ABC):
    capabilities = AdapterCapabilities(
        supports_explicit_base_url=False,
        supports_explicit_model=False,
        supports_noninteractive=True,
        supports_unattended=True,
        default_fairness_classification="native-cli",
        controllability_note="Native/default CLI flow.",
    )

    def __init__(self, config: AgentAdapterConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.agent_name

    def run_command(self, command: list[str], workspace_repo: Path, timeout_seconds: int) -> CommandExecutionResult:
        started = time.monotonic()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        timed_out = False
        if self.config.on_command_start:
            self.config.on_command_start(self.name, command)

        proc = subprocess.Popen(
            command,
            cwd=workspace_repo,
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def _reader(pipe: object, sink: list[str], stream_name: str) -> None:
            if pipe is None:
                return
            for line in pipe:
                sink.append(line)
                if self.config.stream_agent_output and self.config.on_output_line:
                    self.config.on_output_line(self.name, stream_name, line.rstrip("\r\n"))

        stdout_thread = threading.Thread(target=_reader, args=(proc.stdout, stdout_lines, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=_reader, args=(proc.stderr, stderr_lines, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            return_code = proc.wait(timeout=timeout_seconds)
            exit_reason = f"exit:{return_code}"
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            return_code = None
            exit_reason = "timeout"
        finally:
            stdout_thread.join()
            stderr_thread.join()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()

        elapsed = time.monotonic() - started
        catastrophic = timed_out or (return_code is not None and return_code != 0)
        return CommandExecutionResult(
            exit_code=return_code,
            exit_reason=exit_reason,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            elapsed_seconds=elapsed,
            catastrophic_failure=catastrophic,
        )

    @abstractmethod
    def run_task(self, task: BenchmarkTask, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        raise NotImplementedError
