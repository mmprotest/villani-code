from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from villani_code.benchmark.adapters import (
    AgentAdapter,
    AgentAdapterConfig,
    ClaudeCodeAdapter,
    CopilotCLIAdapter,
    OpenCodeAdapter,
    VillaniAdapter,
)
from villani_code.benchmark.environment import BenchmarkEnvironment
from villani_code.benchmark.graders import (
    compute_composite_score,
    compute_forbidden_files_touched,
    compute_unnecessary_files_touched,
    execute_validation_checks,
)
from villani_code.benchmark.models import BenchmarkTask
from villani_code.benchmark.reporting import persist_reports
from villani_code.benchmark.task_loader import load_benchmark_tasks
from villani_code.benchmark.utils import utc_timestamp_slug, write_json


class BenchmarkRunner:
    def __init__(self, output_dir: Path, environment: BenchmarkEnvironment | None = None) -> None:
        self.output_dir = output_dir
        self.environment = environment or BenchmarkEnvironment()

    def run(
        self,
        tasks_dir: Path,
        agents: list[str],
        repo_path: Path,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        timeout_seconds: int | None,
        task_id: str | None = None,
        unsafe: bool = False,
        thinking: str | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        tasks = load_benchmark_tasks(tasks_dir, task_id=task_id)
        run_root = self.output_dir / utc_timestamp_slug()
        run_root.mkdir(parents=True, exist_ok=True)
        result_rows: list[dict[str, Any]] = []

        for agent_name in agents:
            adapter = self._build_adapter(
                agent_name,
                AgentAdapterConfig(
                    agent_name=agent_name,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    timeout_seconds=timeout_seconds or 300,
                    unsafe=unsafe,
                    thinking=thinking,
                    max_tokens=max_tokens,
                ),
            )
            for task in tasks:
                task_timeout = timeout_seconds or task.timeout_seconds
                task = task.model_copy(update={"timeout_seconds": task_timeout})
                workspace = self.environment.create_workspace(repo_path, task.repo_git_ref)
                artifact_dir = run_root / agent_name / task.id
                artifact_dir.mkdir(parents=True, exist_ok=True)

                run_result = adapter.run_task(task, workspace.work_repo, artifact_dir)
                run_result.changed_files = self.environment.collect_changed_files(workspace.work_repo)
                run_result.git_diff = self.environment.collect_git_diff(workspace.work_repo)
                validation_results = execute_validation_checks(task, workspace.work_repo)
                run_result.validation_results = validation_results
                validation_success = all(item.success for item in validation_results)
                task_success = validation_success and not run_result.skipped

                unnecessary = compute_unnecessary_files_touched(run_result.changed_files, task.expected_touched_paths)
                forbidden = compute_forbidden_files_touched(run_result.changed_files, task.forbidden_touched_paths)
                catastrophic = run_result.catastrophic_failure or run_result.exit_reason == "timeout"
                composite = compute_composite_score(
                    task_success=task_success,
                    forbidden_files_touched_count=len(forbidden),
                    unnecessary_files_touched_count=len(unnecessary),
                    catastrophic_failure=catastrophic,
                    elapsed_seconds=run_result.elapsed_seconds,
                )

                scorecard = {
                    "task_success": task_success,
                    "validation_success": validation_success,
                    "elapsed_seconds": run_result.elapsed_seconds,
                    "changed_files_count": len(run_result.changed_files),
                    "unnecessary_files_touched_count": len(unnecessary),
                    "forbidden_files_touched_count": len(forbidden),
                    "catastrophic_failure": catastrophic,
                    "retry_count": run_result.retry_count,
                    "tokens_input": run_result.tokens_input,
                    "tokens_output": run_result.tokens_output,
                    "cost_usd": run_result.cost_usd,
                    "skipped": run_result.skipped,
                    "composite_score": composite,
                }

                self._persist_artifacts(artifact_dir, run_result, scorecard)
                result_rows.append(
                    {
                        "agent_name": run_result.agent_name,
                        "task_id": run_result.task_id,
                        "exit_reason": run_result.exit_reason,
                        "skip_reason": run_result.skip_reason,
                        "changed_files": run_result.changed_files,
                        "scorecard": scorecard,
                    }
                )

        metadata = {
            "model": model,
            "base_url": base_url,
            "agents": agents,
            "seed": seed,
            "tasks_dir": str(tasks_dir),
            "repo_path": str(repo_path),
        }
        persist_reports(run_root, metadata, result_rows)
        self.environment.cleanup()
        return {"output_dir": str(run_root), "metadata": metadata, "results": result_rows}

    def _persist_artifacts(self, artifact_dir: Path, run_result: Any, scorecard: dict[str, Any]) -> None:
        (artifact_dir / "stdout.txt").write_text(run_result.stdout, encoding="utf-8")
        (artifact_dir / "stderr.txt").write_text(run_result.stderr, encoding="utf-8")
        (artifact_dir / "git_diff.patch").write_text(run_result.git_diff, encoding="utf-8")
        write_json(artifact_dir / "changed_files.json", run_result.changed_files)
        write_json(artifact_dir / "validation_results.json", [asdict(v) for v in run_result.validation_results])
        write_json(
            artifact_dir / "metadata.json",
            {
                "agent_name": run_result.agent_name,
                "task_id": run_result.task_id,
                "exit_reason": run_result.exit_reason,
                "skipped": run_result.skipped,
                "skip_reason": run_result.skip_reason,
                "elapsed_seconds": run_result.elapsed_seconds,
                "scorecard": scorecard,
            },
        )

    @staticmethod
    def _build_adapter(agent_name: str, config: AgentAdapterConfig) -> AgentAdapter:
        if agent_name == "villani":
            return VillaniAdapter(config)
        if agent_name == "claude-code":
            return ClaudeCodeAdapter(config)
        if agent_name == "opencode":
            return OpenCodeAdapter(config)
        if agent_name == "copilot-cli":
            return CopilotCLIAdapter(config)
        raise ValueError(f"Unsupported agent: {agent_name}")
