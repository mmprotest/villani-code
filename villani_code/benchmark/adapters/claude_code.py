from __future__ import annotations

import subprocess
import time
from pathlib import Path

from villani_code.benchmark.adapters.base import AgentAdapter, AgentRunResult
from villani_code.benchmark.models import BenchmarkTask
from villani_code.benchmark.utils import command_exists


class ClaudeCodeAdapter(AgentAdapter):
    def run_task(self, task: BenchmarkTask, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        if not command_exists("claude"):
            return AgentRunResult(
                agent_name=self.name,
                task_id=task.id,
                success=False,
                exit_reason="skipped: executable not installed",
                elapsed_seconds=0.0,
                stdout="",
                stderr="",
                changed_files=[],
                git_diff="",
                validation_results=[],
                catastrophic_failure=False,
                tokens_input=None,
                tokens_output=None,
                cost_usd=None,
                raw_artifact_dir=str(artifact_dir),
                skipped=True,
                skip_reason="claude executable not found",
            )

        command = ["claude", "-p", task.instruction]
        command.extend(self.config.extra_args)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=workspace_repo,
                capture_output=True,
                text=True,
                timeout=task.timeout_seconds,
                check=False,
            )
            elapsed = time.monotonic() - started
            return AgentRunResult(
                agent_name=self.name,
                task_id=task.id,
                success=proc.returncode == 0,
                exit_reason=f"exit:{proc.returncode}",
                elapsed_seconds=elapsed,
                stdout=proc.stdout,
                stderr=proc.stderr,
                changed_files=[],
                git_diff="",
                validation_results=[],
                catastrophic_failure=proc.returncode != 0,
                tokens_input=None,
                tokens_output=None,
                cost_usd=None,
                raw_artifact_dir=str(artifact_dir),
                skipped=False,
                skip_reason=None,
            )
        except subprocess.TimeoutExpired as exc:
            return AgentRunResult(
                agent_name=self.name,
                task_id=task.id,
                success=False,
                exit_reason="timeout",
                elapsed_seconds=time.monotonic() - started,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                changed_files=[],
                git_diff="",
                validation_results=[],
                catastrophic_failure=True,
                tokens_input=None,
                tokens_output=None,
                cost_usd=None,
                raw_artifact_dir=str(artifact_dir),
                skipped=False,
                skip_reason=None,
            )
