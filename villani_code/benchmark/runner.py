from __future__ import annotations

import shutil
import time
from pathlib import Path

from villani_code.benchmark.agent_runner import AgentRunner
from villani_code.benchmark.diff_stats import ensure_git_repo, line_stats, list_touched_files
from villani_code.benchmark.models import BenchmarkRunResult, BenchmarkTask, RunStatus, TaskFamily
from villani_code.benchmark.reporting import render_summary_table, summarize, write_results
from villani_code.benchmark.task_loader import load_tasks
from villani_code.benchmark.verifier import run_commands
from villani_code.benchmark.workspace import WorkspaceManager


class BenchmarkRunner:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.workspace = WorkspaceManager()
        self.agent_runner = AgentRunner()

    def list_tasks(self, suite_dir: Path) -> list[BenchmarkTask]:
        return load_tasks(suite_dir)

    def run(
        self,
        suite_dir: Path,
        agent: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        task_id: str | None = None,
    ) -> dict[str, object]:
        tasks = load_tasks(suite_dir, task_id=task_id)
        results: list[BenchmarkRunResult] = []
        for task in tasks:
            results.append(self._run_task(task, agent=agent, model=model, base_url=base_url, api_key=api_key))
        result_path = write_results(results, self.output_dir)
        return {
            "results_path": str(result_path),
            "summary": summarize(results).model_dump(),
            "human_summary": render_summary_table(results),
        }

    def _run_task(self, task: BenchmarkTask, agent: str, model: str | None, base_url: str | None, api_key: str | None) -> BenchmarkRunResult:
        workspace_repo = self.workspace.create(task.task_dir / "repo")
        ensure_git_repo(workspace_repo)
        timeout_seconds = task.max_minutes * 60
        started = time.monotonic()
        timeout = False
        error: str | None = None
        visible_pass = False
        hidden_pass = False
        time_to_first_verify: float | None = None
        num_shell_commands = 0
        num_failed_commands = 0
        verifications: list[str] = []

        try:
            execution = self.agent_runner.run(
                agent=agent,
                prompt=task.prompt,
                repo=workspace_repo,
                timeout_seconds=timeout_seconds,
                model=model,
                base_url=base_url,
                api_key=api_key,
            )
            timeout = execution.timeout
            num_shell_commands = len([line for line in execution.stdout.splitlines() if line.strip().startswith("$")])
            num_failed_commands = len([line for line in execution.stdout.splitlines() if "exit " in line and "exit 0" not in line])

            visible_pass, visible_outcomes, first_verify = run_commands(workspace_repo, task.visible_verification, timeout_seconds)
            if first_verify:
                time_to_first_verify = first_verify - started
            verifications.extend(item.command for item in visible_outcomes)

            if task.family == TaskFamily.REPRO_TEST:
                hidden_pass = self._run_repro_hidden(task, workspace_repo, timeout_seconds)
                verifications.extend(task.hidden_verification)
            else:
                hidden_pass, hidden_outcomes, _ = run_commands(workspace_repo, task.hidden_verification, timeout_seconds)
                verifications.extend(item.command for item in hidden_outcomes)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        touched = list_touched_files(workspace_repo)
        allowlist_ok = all(any(path.startswith(prefix) for prefix in task.allowlist_paths) for path in touched)
        files_touched = len(touched)
        lines_added, lines_deleted = line_stats(workspace_repo)
        runtime_seconds = time.monotonic() - started

        artifacts_ok = self._check_required_artifacts(task, workspace_repo, touched)

        success = int(
            (not timeout or not task.success_policy.fail_on_timeout)
            and (visible_pass or not task.success_policy.require_visible_pass)
            and (hidden_pass or not task.success_policy.require_hidden_pass)
            and (allowlist_ok or not task.success_policy.fail_on_repo_dirty_outside_allowlist)
            and files_touched <= task.max_files_touched
            and artifacts_ok
        )

        return BenchmarkRunResult(
            task_id=task.id,
            agent=agent,
            model=model,
            family=task.family,
            difficulty=task.difficulty,
            success=success,
            visible_pass=visible_pass,
            hidden_pass=hidden_pass,
            runtime_seconds=runtime_seconds,
            files_touched=files_touched,
            touched_file_paths=touched,
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            num_shell_commands=num_shell_commands,
            num_failed_commands=num_failed_commands,
            verifications_run=verifications,
            timeout=timeout,
            error=error,
            time_to_first_verify=time_to_first_verify,
            status=RunStatus.SUCCESS if success else RunStatus.FAILED,
        )

    def _check_required_artifacts(self, task: BenchmarkTask, workspace_repo: Path, touched: list[str]) -> bool:
        expected = set(task.expected_artifacts)
        if "patch" in expected and not touched:
            return False
        if "test" in expected and not any(path.startswith("tests/") for path in touched):
            return False
        return True

    def _run_repro_hidden(self, task: BenchmarkTask, workspace_repo: Path, timeout_seconds: int) -> bool:
        fixed_repo = task.task_dir / "hidden_checks" / "fixed_repo"
        if not fixed_repo.exists():
            return False
        temp_fixed = self.workspace.create(fixed_repo)
        workspace_tests = workspace_repo / "tests"
        fixed_tests = temp_fixed / "tests"
        if fixed_tests.exists():
            shutil.rmtree(fixed_tests)
        shutil.copytree(workspace_tests, fixed_tests)

        # hidden_verification commands are expected to pass on fixed reference
        hidden_pass, _, _ = run_commands(temp_fixed, task.hidden_verification, timeout_seconds)
        return hidden_pass
