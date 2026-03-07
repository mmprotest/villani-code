from __future__ import annotations

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
from villani_code.benchmark.fairness import (
    AdapterCapabilities,
    capability_table_payload,
    classify_run_mode,
)
from villani_code.benchmark.graders import (
    compute_composite_score,
    compute_forbidden_files_touched,
    compute_unnecessary_files_touched,
    execute_validation_checks,
)
from villani_code.benchmark.logging import BenchmarkLogger, render_command
from villani_code.benchmark.models import BenchmarkTask
from villani_code.benchmark.interpretation import derive_interpretation_status
from villani_code.benchmark.preflight import run_benchmark_preflight
from villani_code.benchmark.reporting import aggregate_by_agent, persist_reports
from villani_code.benchmark.task_loader import load_benchmark_tasks, load_task_pack_metadata, resolve_tasks_dir
from villani_code.benchmark.utils import utc_timestamp_slug, write_json


def _summarize_failure_provenance(run_result: Any, validation_results: list[Any]) -> str | None:
    if run_result.skipped:
        return "agent_failure"
    if run_result.exit_reason == "timeout":
        return "timeout"
    if run_result.exit_reason.startswith("exit:") and run_result.exit_reason != "exit:0":
        return "agent_failure"
    failed = [item.failure_provenance for item in validation_results if not item.success and item.failure_provenance]
    if failed:
        return failed[0]
    return None


class BenchmarkRunner:
    _DEFAULT_CAPABILITIES = AdapterCapabilities(
        supports_explicit_base_url=False,
        supports_explicit_model=False,
        supports_noninteractive=True,
        supports_unattended=False,
        default_fairness_classification="native-cli",
        controllability_note="Adapter did not declare capability metadata.",
    )

    def __init__(
        self,
        output_dir: Path,
        environment: BenchmarkEnvironment | None = None,
        logger: BenchmarkLogger | None = None,
        verbose: bool = True,
        stream_agent_output: bool = True,
    ) -> None:
        self.output_dir = output_dir
        self.environment = environment or BenchmarkEnvironment()
        self.logger = logger or BenchmarkLogger(enabled=verbose)
        self.verbose = verbose
        self.stream_agent_output = stream_agent_output

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
        self.logger.info(f"Benchmark started for repo: {repo_path}")
        resolved_tasks_dir = resolve_tasks_dir(tasks_dir)
        pack = load_task_pack_metadata(resolved_tasks_dir)
        tasks = load_benchmark_tasks(resolved_tasks_dir, task_id=task_id)
        preflight = run_benchmark_preflight(resolved_tasks_dir, repo_path, agents)
        for note in preflight.notes:
            self.logger.info(f"Preflight note: {note}")
        for warning in preflight.warnings:
            self.logger.warn(f"Preflight warning: {warning}")
        if not preflight.ok:
            for error in preflight.errors:
                self.logger.error(f"Preflight error: {error}")
            raise ValueError("Benchmark preflight failed. Fix errors before running benchmark.")
        self.logger.info(f"Loaded {len(tasks)} tasks and {len(agents)} agents")
        run_root = self.output_dir / utc_timestamp_slug()
        run_root.mkdir(parents=True, exist_ok=True)
        result_rows: list[dict[str, Any]] = []
        adapter_capabilities: dict[str, AdapterCapabilities] = {}

        total_tasks = len(tasks)
        total_agents = len(agents)
        total_runs = total_tasks * total_agents

        for agent_index, agent_name in enumerate(agents, start=1):
            self.logger.info(f"{self.logger.agent_label(agent_index, total_agents, agent_name)}")
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
                    stream_agent_output=self.stream_agent_output,
                    on_output_line=self.logger.agent_output,
                    on_command_start=lambda run_agent, cmd: self.logger.info(f"Running command ({run_agent}): {render_command(cmd)}"),
                ),
            )
            adapter_capabilities[agent_name] = getattr(
                adapter,
                "capabilities",
                self._DEFAULT_CAPABILITIES,
            )
            agent_rows: list[dict[str, Any]] = []
            for task_index, task in enumerate(tasks, start=1):
                self.logger.info(f"Starting {self.logger.task_label(task_index, total_tasks, task.id)}")
                task_timeout = timeout_seconds or task.timeout_seconds
                task = task.model_copy(update={"timeout_seconds": task_timeout})
                self.logger.info(
                    f"Creating workspace for agent={agent_name}, task={task.id} ({task_index}/{total_tasks}, {agent_index}/{total_agents})"
                )
                workspace = self.environment.create_workspace(repo_path, task.repo_git_ref)
                self.logger.info(f"Workspace ready: {workspace.work_repo}")
                artifact_dir = run_root / agent_name / task.id
                artifact_dir.mkdir(parents=True, exist_ok=True)

                self.logger.info(f"Agent run start: agent={agent_name} task={task.id}")
                run_result = adapter.run_task(task, workspace.work_repo, artifact_dir)
                if run_result.skipped:
                    self.logger.warn(
                        f"Skipping agent '{agent_name}' for task '{task.id}': {run_result.skip_reason or run_result.exit_reason}"
                    )
                self.logger.info(
                    f"Agent finished: {run_result.exit_reason} in {run_result.elapsed_seconds:.1f}s"
                )

                self.logger.info("Collecting changed files")
                run_result.changed_files = self.environment.collect_changed_files(workspace.work_repo)
                run_result.git_diff = self.environment.collect_git_diff(workspace.work_repo)
                self.logger.info(f"Changed files collected: {len(run_result.changed_files)}")

                self.logger.info("Validation start")
                total_checks = len(task.validation_checks)
                validation_results = execute_validation_checks(
                    task,
                    workspace.work_repo,
                    on_check_start=lambda idx, check: self.logger.info(
                        f"Validation {idx + 1}/{total_checks} start: type={check.type.value}"
                    ),
                    on_check_end=lambda idx, _check, result: self.logger.info(
                        f"Validation {idx + 1}/{total_checks} {'passed' if result.success else 'failed'}: exit={result.exit_code}"
                    ),
                )
                run_result.validation_results = validation_results
                validation_success = all(item.success for item in validation_results)
                task_success = validation_success and not run_result.skipped

                self.logger.info("Scoring start")
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
                self.logger.info(
                    f"Scoring done: {composite:.1f} | changed={len(run_result.changed_files)} unnecessary={len(unnecessary)} forbidden={len(forbidden)}"
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
                    "failure_provenance": _summarize_failure_provenance(run_result, validation_results),
                }

                self.logger.info("Artifact persistence start")
                self._persist_artifacts(artifact_dir, run_result, scorecard)
                self.logger.info(f"Wrote artifacts: {artifact_dir}")
                row = {
                    "agent_name": run_result.agent_name,
                    "task_id": run_result.task_id,
                    "exit_reason": run_result.exit_reason,
                    "skip_reason": run_result.skip_reason,
                    "changed_files": run_result.changed_files,
                    "scorecard": scorecard,
                    "failure_provenance": scorecard["failure_provenance"],
                }
                result_rows.append(row)
                agent_rows.append(row)
                self.logger.info(
                    "Task summary: "
                    f"agent={run_result.agent_name} task={run_result.task_id} exit={run_result.exit_reason} "
                    f"validation={'pass' if validation_success else 'fail'} success={task_success} "
                    f"elapsed={run_result.elapsed_seconds:.1f}s score={composite:.1f} changed={len(run_result.changed_files)}"
                )

            self._log_agent_summary(agent_name, agent_rows)

        run_mode, fairness_warning = classify_run_mode(
            agents=agents,
            base_url=base_url,
            model=model,
            capabilities=adapter_capabilities,
        )
        interpretation = derive_interpretation_status(
            pack_classification=pack.classification,
            comparison_suitability=pack.comparison_suitability,
            run_fairness=run_mode,
        )
        metadata = {
            "model": model,
            "base_url": base_url,
            "agents": agents,
            "seed": seed,
            "tasks_dir": str(resolved_tasks_dir),
            "pack_name": pack.name,
            "pack_classification": pack.classification,
            "pack_description": pack.description,
            "comparison_suitability": pack.comparison_suitability,
            "fairness_classification": pack.fairness_classification,
            "environment_notes": "Validation commands normalized via active Python interpreter.",
            "platform": __import__("sys").platform,
            "python_executable": __import__("sys").executable,
            "repo_path": str(repo_path),
            "run_mode": run_mode,
            "run_fairness": run_mode,
            "fairness_warning": fairness_warning,
            "interpretation_status": interpretation.status,
            "interpretation_warning": interpretation.warning,
            "preflight": {"warnings": preflight.warnings, "notes": preflight.notes},
            "config_provenance": {
                "model": "cli flag" if model else "adapter default",
                "base_url": "cli flag" if base_url else "adapter default",
            },
            "agent_capabilities": capability_table_payload(agents, adapter_capabilities),
        }
        persist_reports(run_root, metadata, result_rows)
        agg = aggregate_by_agent(result_rows)
        best_agent = None
        if agg:
            best_agent = max(agg.items(), key=lambda item: item[1].get("average_composite_score", 0.0))[0]
        self.logger.info(
            f"Final summary: output={run_root} tasks={total_tasks} agents={total_agents} runs={total_runs}"
            + (f" best_agent={best_agent}" if best_agent else "")
        )
        self.environment.cleanup()
        return {"output_dir": str(run_root), "metadata": metadata, "results": result_rows}

    def _log_agent_summary(self, agent_name: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            self.logger.info(f"Agent summary: {agent_name} total_tasks=0 success=0 skipped=0 avg_score=0.0 avg_elapsed=0.0s")
            return
        total_tasks = len(rows)
        success_count = sum(1 for row in rows if row["scorecard"]["task_success"])
        skip_count = sum(1 for row in rows if row["scorecard"].get("skipped"))
        avg_score = sum(float(row["scorecard"].get("composite_score", 0.0)) for row in rows) / total_tasks
        avg_elapsed = sum(float(row["scorecard"].get("elapsed_seconds", 0.0)) for row in rows) / total_tasks
        self.logger.info(
            f"Agent summary: {agent_name} total_tasks={total_tasks} success={success_count} "
            f"skipped={skip_count} avg_score={avg_score:.1f} avg_elapsed={avg_elapsed:.1f}s"
        )

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
