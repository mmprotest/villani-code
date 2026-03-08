from __future__ import annotations

import platform
import shutil
import sys
import time
from pathlib import Path

from villani_code.benchmark.adapters import AdapterRunConfig, build_adapter
from villani_code.benchmark.diff_stats import ensure_git_repo, line_stats, list_touched_files
from villani_code.benchmark.manifest import command_set_checksum, repo_checksum
from villani_code.benchmark.models import (
    BENCHMARK_VERSION,
    BenchmarkRunResult,
    BenchmarkTask,
    FailureReason,
    FieldQuality,
    ReproducibilityManifest,
    TaskFamily,
    TelemetryQuality,
)
from villani_code.benchmark.policy import benchmark_asset_integrity, enforce_path_policy
from villani_code.benchmark.reporting import render_summary_table, summarize, write_markdown_report, write_results
from villani_code.benchmark.task_loader import load_tasks
from villani_code.benchmark.verifier import run_commands
from villani_code.benchmark.workspace import WorkspaceManager


class BenchmarkRunner:
    def __init__(self, output_dir: Path, keep_workspace: bool = False, private_suite_dir: Path | None = None) -> None:
        self.output_dir = output_dir
        self.workspace = WorkspaceManager(keep_workspace=keep_workspace)
        self.private_suite_dir = private_suite_dir

    def list_tasks(self, suite_dir: Path, include_private: bool = False, **filters: str | None) -> list[BenchmarkTask]:
        tasks = load_tasks(suite_dir, **filters)
        if include_private and self.private_suite_dir and self.private_suite_dir.exists():
            tasks.extend(load_tasks(self.private_suite_dir, **filters))
        return sorted(tasks, key=lambda t: t.id)

    def run(
        self,
        suite_dir: Path,
        agent: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        task_id: str | None = None,
        repeat: int = 1,
        include_private: bool = False,
        **filters: str | None,
    ) -> dict[str, object]:
        tasks = load_tasks(suite_dir, task_id=task_id, **filters)
        if include_private and self.private_suite_dir and self.private_suite_dir.exists():
            tasks.extend(load_tasks(self.private_suite_dir, task_id=task_id, **filters))
        results: list[BenchmarkRunResult] = []
        for repeat_index in range(repeat):
            for task in tasks:
                results.append(self._run_task(task, agent=agent, model=model, base_url=base_url, api_key=api_key, repeat_index=repeat_index))
        result_path = write_results(results, self.output_dir)
        write_markdown_report(results, self.output_dir / "report.md")
        return {
            "results_path": str(result_path),
            "summary": summarize(results).model_dump(),
            "human_summary": render_summary_table(results),
            "repeat": repeat,
        }

    def _event_metrics(self, events: list[object], started: float, expected_files: list[str]) -> dict[str, object]:
        command_starts = 0
        command_failures = 0
        first_edit: float | None = None
        first_expected_read: float | None = None
        read_paths: set[str] = set()
        for e in events:
            if getattr(e, "type", "") == "command_started":
                command_starts += 1
            if getattr(e, "type", "") == "command_finished":
                exit_code = getattr(e, "payload", {}).get("exit_code")
                if isinstance(exit_code, int) and exit_code != 0:
                    command_failures += 1
            payload = getattr(e, "payload", {})
            event_type = str(payload.get("event") or getattr(e, "type", ""))
            ts = float(payload.get("ts", getattr(e, "timestamp", 0.0)))
            path = payload.get("path")
            if event_type in {"file_edit", "apply_patch", "write_file"} and first_edit is None:
                first_edit = max(0.0, ts - started)
            if event_type in {"file_read", "read_file", "open_file"} and isinstance(path, str):
                read_paths.add(path)
                if path in expected_files and first_expected_read is None:
                    first_expected_read = max(0.0, ts - started)
        return {
            "num_shell_commands": command_starts if command_starts > 0 else None,
            "num_failed_commands": command_failures if command_starts > 0 else None,
            "time_to_first_edit": first_edit,
            "expected_file_first_read_time": first_expected_read,
            "expected_files_found": len(set(expected_files) & read_paths) if expected_files else 0,
            "expected_files_total": len(expected_files),
        }

    def _run_task(self, task: BenchmarkTask, agent: str, model: str | None, base_url: str | None, api_key: str | None, repeat_index: int = 0) -> BenchmarkRunResult:
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
        num_shell_commands: int | None = None
        num_failed_commands: int | None = None
        time_to_first_edit: float | None = None
        expected_files_found: int | None = None
        expected_files_total: int | None = None
        expected_file_first_read_time: float | None = None
        timeout = False
        field_quality_map: dict[str, FieldQuality] = {}

        with self.workspace.create(task.task_dir / "repo") as workspace_repo:
            ensure_git_repo(workspace_repo)
            adapter = build_adapter(agent)
            if not benchmark_asset_integrity(task.task_dir):
                failure_reason = FailureReason.BENCHMARK_ERROR
                error = "task assets integrity check failed"

            manifest = ReproducibilityManifest(
                benchmark_version=BENCHMARK_VERSION,
                task_id=task.id,
                task_version=task.task_version,
                task_checksum=task.task_checksum or "",
                repo_checksum=repo_checksum(task.task_dir / "repo"),
                visible_check_checksum=command_set_checksum(task.visible_verification),
                hidden_check_checksum=command_set_checksum(task.hidden_verification),
                adapter_name=adapter.name,
                adapter_version=adapter.version,
                timeout_seconds=timeout_seconds,
                repeat_index=repeat_index,
                platform=platform.platform(),
                python_version=sys.version,
                agent_name=agent,
                model_name=model,
                provider="custom" if base_url else None,
                base_url=base_url,
                env_allowlist=task.env_allowlist,
                workspace_preserved=self.workspace.keep_workspace,
            )
            manifest_path = self.output_dir / f"manifest_{task.id}_{repeat_index}_{int(time.time()*1000)}.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                execution = adapter.run(
                    AdapterRunConfig(
                        prompt=task.prompt,
                        workspace_repo=workspace_repo,
                        timeout_seconds=timeout_seconds,
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                    )
                )
                timeout = execution.timeout
                telemetry_quality = execution.telemetry_quality
                field_quality_map = execution.telemetry_field_quality_map

                if field_quality_map.get("num_shell_commands") == FieldQuality.EXACT:
                    metrics = self._event_metrics(execution.events, started, task.metadata.expected_files)
                    num_shell_commands = metrics["num_shell_commands"]
                    num_failed_commands = metrics["num_failed_commands"]
                    time_to_first_edit = metrics["time_to_first_edit"]
                    expected_file_first_read_time = metrics["expected_file_first_read_time"]
                    expected_files_found = metrics["expected_files_found"]
                    expected_files_total = metrics["expected_files_total"]

                visible_pass, visible_outcomes, first_verify, l_verify = run_commands(workspace_repo, task.visible_verification, timeout_seconds)
                if first_verify:
                    time_to_first_verify = first_verify - started
                last_verify = (l_verify - started) if l_verify else None
                verifications.extend(item.command for item in visible_outcomes)
                if not visible_pass:
                    failure_reason = FailureReason.VISIBLE_VERIFICATION_FAILED

                if task.family == TaskFamily.REPRO_TEST:
                    hidden_pass, invalid_repro = self._run_repro_hidden(task, workspace_repo, timeout_seconds)
                    if not hidden_pass:
                        failure_reason = FailureReason.INVALID_REPRO_TEST if invalid_repro else FailureReason.HIDDEN_VERIFICATION_FAILED
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
            policy_result = enforce_path_policy(touched, task.allowlist_paths, task.forbidden_paths)
            files_touched = len(touched)
            lines_added, lines_deleted = line_stats(workspace_repo)
            runtime_seconds = time.monotonic() - started
            artifacts_ok = self._check_required_artifacts(task, touched)

            if expected_files_total is None:
                expected_files_total = len(task.metadata.expected_files)
            if expected_files_found is None:
                expected_files_found = sum(1 for rel in task.metadata.expected_files if (workspace_repo / rel).exists())

            if timeout:
                failure_reason = FailureReason.TIMEOUT
            elif not policy_result.allowlist_ok or not policy_result.forbidden_ok:
                failure_reason = FailureReason.FORBIDDEN_EDIT
            elif not artifacts_ok:
                failure_reason = FailureReason.MISSING_ARTIFACT
            elif error:
                failure_reason = failure_reason or FailureReason.AGENT_CRASH

            success = int(
                (not timeout or not task.success_policy.fail_on_timeout)
                and (visible_pass or not task.success_policy.require_visible_pass)
                and (hidden_pass or not task.success_policy.require_hidden_pass)
                and (policy_result.allowlist_ok and policy_result.forbidden_ok or not task.success_policy.fail_on_repo_dirty_outside_allowlist)
                and files_touched <= task.max_files_touched
                and artifacts_ok
                and error is None
            )
            manifest.telemetry_quality = telemetry_quality
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

            hidden_failed = any("hidden" in c for c in verifications[-len(task.hidden_verification) :]) if task.hidden_verification else False
            return BenchmarkRunResult(
                benchmark_track=task.benchmark_track,
                task_id=task.id,
                task_version=task.task_version,
                task_family=task.family,
                task_difficulty=task.difficulty,
                task_language=task.language,
                task_source_type=task.source_type,
                task_tags=task.tags,
                task_checksum=task.task_checksum or "",
                agent_name=agent,
                adapter_name=adapter.name,
                adapter_version=adapter.version,
                adapter_capability=adapter.capability,
                fairness_classification=adapter.fairness_classification,
                fairness_notes=adapter.fairness_notes,
                telemetry_capability=adapter.telemetry_capability,
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
                verification_attempt_count=len(verifications),
                time_to_first_edit=time_to_first_edit,
                time_to_first_verify=time_to_first_verify,
                last_verification_time=last_verify,
                expected_files_found=expected_files_found,
                expected_files_total=expected_files_total,
                expected_file_first_read_time=expected_file_first_read_time,
                self_corrected_after_failed_verify=(visible_pass and hidden_pass and not hidden_failed) if verifications else None,
                touched_irrelevant_files=sum(1 for p in touched if not any(p.startswith(a) for a in task.allowlist_paths)),
                telemetry_quality=telemetry_quality,
                telemetry_field_quality_map=field_quality_map,
                workspace_preserved=self.workspace.keep_workspace,
                reproducibility_manifest_path=str(manifest_path),
                repeat_index=repeat_index,
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
            return False, True
        temp_root = workspace_repo.parent / "fixed"
        shutil.copytree(fixed_repo, temp_root)
        workspace_tests = workspace_repo / "tests"
        fixed_tests = temp_root / "tests"
        if fixed_tests.exists():
            shutil.rmtree(fixed_tests)
        shutil.copytree(workspace_tests, fixed_tests)

        broken_pass, broken_outcomes, _, _ = run_commands(workspace_repo, task.hidden_verification, timeout_seconds)
        fixed_pass, fixed_outcomes, _, _ = run_commands(temp_root, task.hidden_verification, timeout_seconds)
        all_output = "\n".join(o.stdout + o.stderr for o in broken_outcomes + fixed_outcomes)
        syntax_noise = any(token in all_output for token in ["SyntaxError", "ImportError", "ModuleNotFoundError"])
        meaningful = any("assert" in (o.stdout + o.stderr).lower() or "failed" in (o.stdout + o.stderr).lower() for o in broken_outcomes)
        valid = (not broken_pass) and fixed_pass and meaningful and not syntax_noise
        return valid, not valid
