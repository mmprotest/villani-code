from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from villani_code.benchmark.adapters import AdapterRunConfig, build_adapter
from villani_code.benchmark.diff_stats import ensure_git_repo, line_stats, list_touched_files
from villani_code.benchmark.models import BENCHMARK_VERSION, BenchmarkRunResult, BenchmarkTask, FailureReason, ReproducibilityManifest, TaskFamily, TelemetryQuality
from villani_code.benchmark.reporting import render_summary_table, summarize, write_markdown_report, write_results
from villani_code.benchmark.task_loader import load_tasks
from villani_code.benchmark.verifier import run_commands
from villani_code.benchmark.workspace import WorkspaceManager


class BenchmarkRunner:
    def __init__(self, output_dir: Path, keep_workspace: bool = False) -> None:
        self.output_dir = output_dir
        self.workspace = WorkspaceManager(keep_workspace=keep_workspace)

    def list_tasks(self, suite_dir: Path, **filters: str | None) -> list[BenchmarkTask]:
        return load_tasks(suite_dir, **filters)

    def run(self, suite_dir: Path, agent: str, model: str | None, base_url: str | None, api_key: str | None, task_id: str | None = None, repeat: int = 1, **filters: str | None) -> dict[str, object]:
        tasks = load_tasks(suite_dir, task_id=task_id, **filters)
        results: list[BenchmarkRunResult] = []
        for _ in range(repeat):
            for task in tasks:
                results.append(self._run_task(task, agent=agent, model=model, base_url=base_url, api_key=api_key))
        result_path = write_results(results, self.output_dir)
        write_markdown_report(results, self.output_dir / "report.md")
        return {"results_path": str(result_path), "summary": summarize(results).model_dump(), "human_summary": render_summary_table(results)}

    def _run_task(self, task: BenchmarkTask, agent: str, model: str | None, base_url: str | None, api_key: str | None) -> BenchmarkRunResult:
        timeout_seconds = task.max_minutes * 60
        started = time.monotonic()
        failure_reason: FailureReason | None = None
        error: str | None = None
        visible_pass = False
        hidden_pass = False
        verifications: list[str] = []
        time_to_first_verify: float | None = None
        last_verify: float | None = None
        telemetry_quality = TelemetryQuality.UNAVAILABLE
        inferred_fields: list[str] = []
        num_shell_commands: int | None = None
        num_failed_commands: int | None = None
        timeout = False

        with self.workspace.create(task.task_dir / "repo") as workspace_repo:
            ensure_git_repo(workspace_repo)
            adapter = build_adapter(agent)
            manifest = ReproducibilityManifest(
                benchmark_version=BENCHMARK_VERSION,
                task_id=task.id,
                task_version=task.task_version,
                task_checksum=task.task_checksum or "",
                platform=platform.platform(),
                python_version=sys.version,
                timeout_seconds=timeout_seconds,
                agent_name=agent,
                model_name=model,
                provider="custom" if base_url else None,
                base_url=base_url,
            )
            manifest_path = self.output_dir / f"manifest_{task.id}_{int(time.time()*1000)}.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            try:
                execution = adapter.run(AdapterRunConfig(prompt=task.prompt, workspace_repo=workspace_repo, timeout_seconds=timeout_seconds, model=model, base_url=base_url, api_key=api_key))
                timeout = execution.timeout
                telemetry_quality = execution.telemetry_quality
                num_shell_commands = len(execution.events) if execution.events else None
                if num_shell_commands is None:
                    inferred_fields.append("num_shell_commands")
                num_failed_commands = 0 if execution.exit_code == 0 else 1
                visible_pass, visible_outcomes, first_verify, l_verify = run_commands(workspace_repo, task.visible_verification, timeout_seconds)
                if first_verify:
                    time_to_first_verify = first_verify - started
                last_verify = (l_verify - started) if l_verify else None
                verifications.extend(item.command for item in visible_outcomes)
                if not visible_pass:
                    failure_reason = FailureReason.VISIBLE_VERIFICATION_FAILED

                if task.family == TaskFamily.REPRO_TEST:
                    hidden_pass, repro_reason = self._run_repro_hidden(task, workspace_repo, timeout_seconds)
                    if not hidden_pass:
                        failure_reason = FailureReason.INVALID_REPRO_TEST if repro_reason else FailureReason.HIDDEN_VERIFICATION_FAILED
                else:
                    hidden_pass, hidden_outcomes, _, l_verify_hidden = run_commands(workspace_repo, task.hidden_verification, timeout_seconds)
                    verifications.extend(item.command for item in hidden_outcomes)
                    if l_verify_hidden:
                        last_verify = l_verify_hidden - started
                    if visible_pass and not hidden_pass and failure_reason is None:
                        failure_reason = FailureReason.HIDDEN_VERIFICATION_FAILED
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                failure_reason = FailureReason.BENCHMARK_ERROR

            touched = list_touched_files(workspace_repo)
            allowlist_ok = all(any(path.startswith(prefix) for prefix in task.allowlist_paths) for path in touched)
            files_touched = len(touched)
            lines_added, lines_deleted = line_stats(workspace_repo)
            runtime_seconds = time.monotonic() - started
            artifacts_ok = self._check_required_artifacts(task, touched)

            if timeout:
                failure_reason = FailureReason.TIMEOUT
            elif not allowlist_ok:
                failure_reason = FailureReason.FORBIDDEN_EDIT
            elif not artifacts_ok:
                failure_reason = FailureReason.MISSING_ARTIFACT
            elif error:
                failure_reason = failure_reason or FailureReason.AGENT_CRASH

            success = int(
                (not timeout or not task.success_policy.fail_on_timeout)
                and (visible_pass or not task.success_policy.require_visible_pass)
                and (hidden_pass or not task.success_policy.require_hidden_pass)
                and (allowlist_ok or not task.success_policy.fail_on_repo_dirty_outside_allowlist)
                and files_touched <= task.max_files_touched
                and artifacts_ok
                and error is None
            )

            return BenchmarkRunResult(
                task_id=task.id,
                task_family=task.family,
                task_difficulty=task.difficulty,
                task_language=task.language,
                task_checksum=task.task_checksum or "",
                agent_name=agent,
                model_name=model,
                provider_label=base_url,
                success=success,
                visible_pass=visible_pass,
                hidden_pass=hidden_pass,
                runtime_seconds=runtime_seconds,
                timeout=timeout,
                failure_reason=None if success else failure_reason,
                error=error,
                touched_file_paths=touched,
                files_touched=files_touched,
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                num_shell_commands=num_shell_commands,
                num_failed_commands=num_failed_commands,
                verifications_run=verifications,
                time_to_first_verify=time_to_first_verify,
                last_verification_time=last_verify,
                telemetry_quality=telemetry_quality,
                workspace_preserved=self.workspace.keep_workspace,
                reproducibility_manifest_path=str(manifest_path),
                inferred_fields=inferred_fields,
            )

    def _check_required_artifacts(self, task: BenchmarkTask, touched: list[str]) -> bool:
        expected = set(task.expected_artifacts)
        if "patch" in expected and not touched:
            return False
        if "test" in expected and not any(path.startswith("tests/") for path in touched):
            return False
        return True

    def _run_repro_hidden(self, task: BenchmarkTask, workspace_repo: Path, timeout_seconds: int) -> tuple[bool, bool]:
        fixed_repo = task.task_dir / "hidden_checks" / "fixed_repo"
        if not fixed_repo.exists():
            return False, False
        temp_root = workspace_repo.parent / "fixed"
        shutil.copytree(fixed_repo, temp_root)
        workspace_tests = workspace_repo / "tests"
        fixed_tests = temp_root / "tests"
        if fixed_tests.exists():
            shutil.rmtree(fixed_tests)
        shutil.copytree(workspace_tests, fixed_tests)

        # fail on broken workspace and pass on fixed; also reject syntax/import failures.
        broken_pass, broken_outcomes, _, _ = run_commands(workspace_repo, task.hidden_verification, timeout_seconds)
        fixed_pass, fixed_outcomes, _, _ = run_commands(temp_root, task.hidden_verification, timeout_seconds)
        meaningful = any("assert" in (o.stdout + o.stderr) or "failed" in (o.stdout + o.stderr).lower() for o in broken_outcomes)
        syntax_noise = any("SyntaxError" in (o.stderr + o.stdout) or "ImportError" in (o.stderr + o.stdout) for o in broken_outcomes + fixed_outcomes)
        valid = (not broken_pass) and fixed_pass and meaningful and not syntax_noise
        return valid, not valid
