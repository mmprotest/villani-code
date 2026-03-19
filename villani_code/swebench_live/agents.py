from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from villani_code.swebench_live.types import AgentConfig, AgentInvocationResult


class PreparedInstance(Protocol):
    repo_path: str

    def run_process(
        self,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
        stdout_path: Path,
        stderr_path: Path,
    ) -> AgentInvocationResult:
        raise NotImplementedError


class AgentRunner(ABC):
    @abstractmethod
    def run(self, prepared_instance: PreparedInstance, prompt: str, config: AgentConfig, log_dir: Path) -> AgentInvocationResult:
        raise NotImplementedError


class VillaniAgentRunner(AgentRunner):
    def build_command(self, prompt: str, repo_path: str, config: AgentConfig) -> list[str]:
        command = [
            "python",
            "-m",
            "villani_code.cli",
            "run",
            prompt,
            "--repo",
            repo_path,
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
        return dict(config.env_overrides)

    def run(self, prepared_instance: PreparedInstance, prompt: str, config: AgentConfig, log_dir: Path) -> AgentInvocationResult:
        command = self.build_command(prompt, prepared_instance.repo_path, config)
        return prepared_instance.run_process(
            command=command,
            cwd=prepared_instance.repo_path,
            env=self.build_env(config),
            timeout_seconds=config.timeout_seconds,
            stdout_path=log_dir / "agent_stdout.txt",
            stderr_path=log_dir / "agent_stderr.txt",
        )
