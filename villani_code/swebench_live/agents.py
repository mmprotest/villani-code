from __future__ import annotations

import os
import shlex
import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from villani_code.swebench_live.io_utils import run_logged_subprocess
from villani_code.swebench_live.types import AgentConfig, AgentInvocationResult, WorkspaceMapping


class PreparedInstance(Protocol):
    workspace: WorkspaceMapping


class AgentRunner(ABC):
    @abstractmethod
    def run(self, prepared_instance: PreparedInstance, prompt: str, config: AgentConfig, log_dir: Path) -> AgentInvocationResult:
        raise NotImplementedError


class VillaniAgentRunner(AgentRunner):
    def resolve_runner_prefix(self, config: AgentConfig) -> list[str]:
        if config.runner_command_prefix:
            prefix = list(config.runner_command_prefix)
        else:
            prefix = [config.runner_python or sys.executable, "-m", "villani_code.cli"]
        self._validate_runner_prefix(prefix)
        return prefix

    def _validate_runner_prefix(self, prefix: list[str]) -> None:
        if not prefix:
            raise RuntimeError("Runner command prefix must not be empty")
        executable = prefix[0]
        if any(sep in executable for sep in (os.sep, "/", "\\")):
            if not Path(executable).exists():
                raise RuntimeError(
                    f"Runner executable does not exist: {executable}. "
                    "Pass a valid --runner-python or --runner-command-prefix."
                )
            return
        if shutil.which(executable) is None:
            raise RuntimeError(
                f"Runner executable is not on PATH: {executable}. "
                "Pass a valid --runner-python or --runner-command-prefix."
            )

    def build_command(self, prompt: str, repo_path: Path, config: AgentConfig) -> list[str]:
        command = [
            *self.resolve_runner_prefix(config),
            "run",
            prompt,
            "--repo",
            str(repo_path),
            "--provider",
            config.provider,
            "--model",
            config.model,
            "--no-stream",
        ]
        if config.base_url:
            command.extend(["--base-url", config.base_url])
        if config.api_key:
            command.extend(["--api-key", config.api_key])
        return command

    def build_env(self, config: AgentConfig) -> dict[str, str]:
        env = os.environ.copy()
        env.update(config.env_overrides)
        return env

    def run(self, prepared_instance: PreparedInstance, prompt: str, config: AgentConfig, log_dir: Path) -> AgentInvocationResult:
        host_repo_path = prepared_instance.workspace.host_repo_path
        if not host_repo_path.exists():
            raise RuntimeError(
                f"External runner cannot access benchmark repo path: {host_repo_path}. "
                "Check the task-to-host repo path mapping."
            )
        if not host_repo_path.is_dir():
            raise RuntimeError(f"External runner repo path is not a directory: {host_repo_path}")

        command = self.build_command(prompt, host_repo_path, config)
        runner_cwd = config.runner_cwd or host_repo_path
        if not runner_cwd.exists():
            raise RuntimeError(f"Runner cwd does not exist: {runner_cwd}")
        process = run_logged_subprocess(
            command,
            cwd=runner_cwd,
            env=self.build_env(config),
            timeout_seconds=config.timeout_seconds,
            stdout_path=log_dir / "agent_stdout.txt",
            stderr_path=log_dir / "agent_stderr.txt",
        )
        return AgentInvocationResult(
            exit_code=process.exit_code,
            timed_out=process.timed_out,
            duration_seconds=process.duration_seconds,
            stdout_path=process.stdout_path,
            stderr_path=process.stderr_path,
            command=process.command,
            sanitized_command=process.sanitized_command,
            error_summary=self._summarize_process_failure(process),
        )

    @staticmethod
    def parse_runner_command_prefix(raw: str) -> tuple[str, ...]:
        return tuple(shlex.split(raw, posix=(os.name != "nt")))

    @staticmethod
    def _summarize_process_failure(process: object) -> str | None:
        exit_code = getattr(process, "exit_code", None)
        timed_out = bool(getattr(process, "timed_out", False))
        duration = float(getattr(process, "duration_seconds", 0.0))
        stderr = str(getattr(process, "stderr", "") or "").strip()
        stdout = str(getattr(process, "stdout", "") or "").strip()
        if timed_out:
            return f"timed out after {duration:.2f}s"
        if exit_code in {0, None}:
            return None
        detail = stderr or stdout
        if detail:
            return f"exit code {exit_code}: {detail.splitlines()[0][:240]}"
        return f"exit code {exit_code}"
