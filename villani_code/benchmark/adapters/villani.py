from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from villani_code.benchmark.adapters.base import AgentAdapter, AgentRunResult
from villani_code.benchmark.models import BenchmarkTask


class VillaniAdapter(AgentAdapter):
    def run_task(self, task: BenchmarkTask, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        started = time.monotonic()
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

        env = os.environ.copy()
        try:
            proc = subprocess.run(
                command,
                cwd=workspace_repo,
                capture_output=True,
                text=True,
                timeout=task.timeout_seconds,
                env=env,
                check=False,
            )
            exit_reason = f"exit:{proc.returncode}"
            catastrophic = proc.returncode != 0
            success = proc.returncode == 0
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            exit_reason = "timeout"
            catastrophic = True
            success = False
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""

        elapsed = time.monotonic() - started
        return AgentRunResult(
            agent_name=self.name,
            task_id=task.id,
            success=success,
            exit_reason=exit_reason,
            elapsed_seconds=elapsed,
            stdout=stdout,
            stderr=stderr,
            changed_files=[],
            git_diff="",
            validation_results=[],
            catastrophic_failure=catastrophic,
            tokens_input=None,
            tokens_output=None,
            cost_usd=None,
            raw_artifact_dir=str(artifact_dir),
            skipped=False,
            skip_reason=None,
        )
