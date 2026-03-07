from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.adapters.base import AgentAdapter, AgentRunResult
from villani_code.benchmark.fairness import AdapterCapabilities
from villani_code.benchmark.models import BenchmarkTask
from villani_code.benchmark.utils import command_exists


class CopilotCLIAdapter(AgentAdapter):
    capabilities = AdapterCapabilities(
        supports_explicit_base_url=False,
        supports_explicit_model=False,
        supports_noninteractive=True,
        supports_unattended=False,
        default_fairness_classification="native-cli",
        controllability_note="Native CLI flow unless separately configured outside this adapter.",
    )

    def run_task(self, task: BenchmarkTask, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        if not command_exists("copilot"):
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
                skip_reason="copilot executable not found",
            )

        command = ["copilot", "-p", task.instruction, *self.config.extra_args]
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
