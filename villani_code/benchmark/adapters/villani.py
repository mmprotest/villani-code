from __future__ import annotations

import sys
from pathlib import Path

from villani_code.benchmark.adapters.base import AgentAdapter, AgentRunResult
from villani_code.benchmark.fairness import AdapterCapabilities
from villani_code.benchmark.models import BenchmarkTask


class VillaniAdapter(AgentAdapter):
    capabilities = AdapterCapabilities(
        supports_explicit_base_url=True,
        supports_explicit_model=True,
        supports_noninteractive=True,
        supports_unattended=True,
        default_fairness_classification="same-backend",
        controllability_note="Explicit base_url and model are configurable.",
    )

    def run_task(self, task: BenchmarkTask, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        command = [
            sys.executable,
            "-m",
            "villani_code.cli",
            "run",
            task.instruction,
            "--base-url",
            self.config.base_url or "",
            "--model",
            self.config.model or "",
            "--repo",
            str(workspace_repo),
            "--provider",
            self.config.provider or "anthropic",
            "--no-stream",
        ]
        if self.config.max_tokens is not None:
            command.extend(["--max-tokens", str(self.config.max_tokens)])
        if self.config.unsafe:
            command.append("--unsafe")
        if self.config.thinking:
            command.extend(["--thinking", self.config.thinking])
        if self.config.api_key:
            command.extend(["--api-key", self.config.api_key])

        proc_result = self.run_command(command, workspace_repo, task.timeout_seconds)
        return AgentRunResult(
            agent_name=self.name,
            task_id=task.id,
            success=proc_result.exit_code == 0,
            exit_reason=proc_result.exit_reason,
            elapsed_seconds=proc_result.elapsed_seconds,
            stdout=proc_result.stdout,
            stderr=proc_result.stderr,
            changed_files=[],
            git_diff="",
            validation_results=[],
            catastrophic_failure=proc_result.catastrophic_failure,
            tokens_input=None,
            tokens_output=None,
            cost_usd=None,
            raw_artifact_dir=str(artifact_dir),
            skipped=False,
            skip_reason=None,
            exit_code=proc_result.exit_code,
            command=command,
        )
