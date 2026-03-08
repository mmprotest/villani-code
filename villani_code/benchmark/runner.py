from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import time
from pathlib import Path

from villani_code.benchmark.agents import build_agent_runner
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
from villani_code.benchmark.policy import (
    benchmark_asset_integrity,
    enforce_path_policy,
    filter_meaningful_touched_paths,
)
from villani_code.benchmark.reporting import render_summary_table, summarize, write_markdown_report, write_results
from villani_code.benchmark.task_loader import load_tasks
from villani_code.benchmark.verifier import run_commands
from villani_code.benchmark.workspace import WorkspaceManager


class BenchmarkRunner:
    def __init__(self, output_dir: Path, keep_workspace: bool = False, private_suite_dir: Path | None = None) -> None:
        self.output_dir = output_dir
        self.workspace = WorkspaceManager(keep_workspace=keep_workspace)
        self.private_suite_dir = private_suite_dir

    @staticmethod
    def _log(message: str) -> None:
        print(f"[benchmark] {message}")

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
        provider: str | None = None,
        task_id: str | None = None,
        repeat: int = 1,
        include_private: bool = False,
        **filters: str | None,
    ) -> dict[str, object]:
        tasks = load_tasks(suite_dir, task_id=task_id, **filters)
        if include_private and self.private_suite_dir and self.private_suite_dir.exists():
            tasks.extend(load_tasks(self.private_suite_dir, task_id=task_id, **filters))
        results: list[BenchmarkRunResult] = []
        self._log(
            f"start suite={suite_dir} tasks={len(tasks)} agent={agent} model={model or '-'} provider={provider or '-'} base_url={base_url or '-'} output_dir={self.output_dir}"
        )
        for repeat_index in range(repeat):
            for index, task in enumerate(tasks, start=1):
                self._log(
                    f"{index}/{len(tasks)} agent={agent} task={task.id} bucket={task.metadata.benchmark_bucket} type={task.metadata.task_type or '-'}"
                )
                results.append(
                    self._run_task(
                        task,
                        agent=agent,
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        provider=provider,
                        repeat_index=repeat_index,
                    )
                )
        result_path = write_results(results, self.output_dir)
        write_markdown_report(results, self.output_dir / "report.md")
        summary = summarize(results).model_dump()
        self._log(
            f"complete successes={summary['successes']}/{summary['total_tasks']} success_rate={summary['success_rate']:.3f} results={result_path}"
        )
        return {
            "results_path": str(result_path),
            "summary": summary,
            "human_summary": render_summary_table(results),
            "repeat": repeat,
        }

    def _event_metrics(self, events: list[object], started: float, expected_files: list[str], visible_commands: list[str], hidden_commands: list[str]) -> dict[str, object]:
        command_starts = 0
        command_failures = 0
        first_edit: float | None = None
        first_expected_read: float | None = None
        read_paths: set[str] = set()
        touched_paths: set[str] = set()
        tool_calls_total = 0
        file_reads = 0
        file_writes = 0
        patch_attempts = 0
        test_runs = 0
        number_of_turns = 0
        retries_after_failure = 0
        verification_seen = False
        verification_failed = False

        verification_commands = set(visible_commands + hidden_commands)
        for e in events:
            etype = getattr(e, "type", "")
            payload = getattr(e, "payload", {})
            if etype == "command_started":
                command_starts += 1
                command = str(payload.get("command", ""))
                if any(cmd in command for cmd in verification_commands):
                    test_runs += 1
                    verification_seen = True
            if etype == "command_finished":
                exit_code = payload.get("exit_code")
                if isinstance(exit_code, int) and exit_code != 0:
                    command_failures += 1
                    if verification_seen:
                        verification_failed = True
                elif isinstance(exit_code, int) and exit_code == 0 and verification_failed:
                    retries_after_failure += 1
                    verification_failed = False

            event_type = str(payload.get("event") or etype)
            ts = float(payload.get("ts", getattr(e, "timestamp", 0.0)))
            path = payload.get("path")

            if event_type in {"tool_call", "tool_invocation", "tool_result", "tool_use"}:
                tool_calls_total += 1
            if event_type in {"model_message", "assistant_message", "turn_started", "turn_completed"}:
                number_of_turns += 1
            if event_type in {"file_edit", "apply_patch", "write_file"}:
                file_writes += 1
                if event_type == "apply_patch":
                    patch_attempts += 1
                if first_edit is None:
                    first_edit = max(0.0, ts - started)
            if event_type in {"file_read", "read_file", "open_file"} and isinstance(path, str):
                file_reads += 1
                read_paths.add(path)
                if path in expected_files and first_expected_read is None:
                    first_expected_read = max(0.0, ts - started)
            if isinstance(path, str):
                touched_paths.add(path)

        if number_of_turns == 0:
            number_of_turns = None
        return {
            "num_shell_commands": command_starts if command_starts > 0 else None,
            "num_failed_commands": command_failures if command_starts > 0 else None,
            "time_to_first_edit": first_edit,
            "expected_file_first_read_time": first_expected_read,
            "expected_files_found": len(set(expected_files) & read_paths) if expected_files else 0,
            "expected_files_total": len(expected_files),
            "tool_calls_total": tool_calls_total if tool_calls_total > 0 else None,
            "file_reads": file_reads if file_reads > 0 else None,
            "file_writes": file_writes if file_writes > 0 else None,
            "patch_attempts": patch_attempts if patch_attempts > 0 else None,
            "test_runs": test_runs if test_runs > 0 else None,
            "number_of_turns": number_of_turns,
            "retries_after_failure": retries_after_failure if retries_after_failure > 0 else 0,
        }

    @staticmethod
    def _stderr_snippet(stderr: str, max_len: int = 240) -> str:
        compact = " ".join(stderr.strip().split())
        if len(compact) <= max_len:
            return compact
        return compact[: max_len - 3] + "..."

    def _run_task(
        self,
        task: BenchmarkTask,
        agent: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        repeat_index: int = 0,
    ) -> BenchmarkRunResult:
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
        number_of_turns: int | None = None
        tool_calls_total: int | None = None
        file_reads: int | None = None
        file_writes: int | None = None
        patch_attempts: int | None = None
        test_runs: int | None = None
        retries_after_failure: int | None = None
        agent_exit_code: int | None = None
        agent_stderr_preview: str | None = None

        with self.workspace.create(task.task_dir / "repo") as workspace_repo:
            ensure_git_repo(workspace_repo)
            adapter = build_agent_runner(agent)
            if model and not agent.startswith(("cmd:", "shell:")) and not adapter.supports_model_override:
                raise ValueError(f"Agent '{agent}' does not support model override; benchmark cannot ensure fair comparison.")
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
                provider=provider or ("openai" if base_url else None),
                base_url=base_url,
                env_allowlist=task.env_allowlist,
                workspace_preserved=self.workspace.keep_workspace,
            )
            manifest_path = self.output_dir / f"manifest_{task.id}_{repeat_index}_{int(time.time()*1000)}.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                self._log("starting agent process...")
                execution = adapter.run_agent(
                    repo_path=workspace_repo,
                    prompt=task.prompt,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    provider=provider,
                    timeout=timeout_seconds,
                )
                timeout = execution.timeout
                telemetry_quality = execution.telemetry_quality
                field_quality_map = execution.telemetry_field_quality_map
                agent_exit_code = execution.exit_code
                agent_stderr_preview = self._stderr_snippet(execution.stderr, max_len=400) if execution.stderr else None
                self._log(
                    f"agent exit_code={execution.exit_code} runtime={execution.runtime_seconds:.1f}s, running verification..."
                )

                if not execution.timeout and execution.exit_code not in {None, 0}:
                    error = f"agent process exited with code {execution.exit_code}"
                    if agent_stderr_preview:
                        error = f"{error}; stderr: {agent_stderr_preview}"
                    failure_reason = FailureReason.AGENT_CRASH
                    self._log(f"agent crash: {agent_stderr_preview or 'no stderr preview available'}")

                metrics = self._event_metrics(execution.events, started, task.metadata.expected_files, task.visible_verification, task.hidden_verification)
                if field_quality_map.get("num_shell_commands") in {FieldQuality.EXACT, FieldQuality.INFERRED}:
                    num_shell_commands = metrics["num_shell_commands"]
                    num_failed_commands = metrics["num_failed_commands"]
                    time_to_first_edit = metrics["time_to_first_edit"]
                    expected_file_first_read_time = metrics["expected_file_first_read_time"]
                    expected_files_found = metrics["expected_files_found"]
                    expected_files_total = metrics["expected_files_total"]
                    number_of_turns = metrics["number_of_turns"]
                    tool_calls_total = metrics["tool_calls_total"]
                    file_reads = metrics["file_reads"]
                    file_writes = metrics["file_writes"]
                    patch_attempts = metrics["patch_attempts"]
                    test_runs = metrics["test_runs"]
                    retries_after_failure = metrics["retries_after_failure"]

                if error is None:
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
                self._log(f"benchmark harness error: {self._stderr_snippet(error, max_len=300)}")

            raw_touched = list_touched_files(workspace_repo)
            ignored_junk_paths = [path for path in raw_touched if path not in filter_meaningful_touched_paths(raw_touched)]
            touched = filter_meaningful_touched_paths(raw_touched)
            policy_result = enforce_path_policy(
                touched,
                task.allowlist_paths,
                task.forbidden_paths,
                expected_paths=task.metadata.expected_files,
                family=task.family.value,
                task_type=task.metadata.task_type,
            )
            policy_result.ignored_junk_paths = ignored_junk_paths
            files_touched = len(touched)
            lines_added, lines_deleted = line_stats(workspace_repo)
            runtime_seconds = time.monotonic() - started
            artifacts_ok, artifact_failure_detail = self._check_required_artifacts(task, touched)

            if expected_files_total is None:
                expected_files_total = len(task.metadata.expected_files)
            if expected_files_found is None:
                expected_files_found = sum(1 for rel in task.metadata.expected_files if (workspace_repo / rel).exists())

            solved_checks_passed = visible_pass and hidden_pass
            policy_warning = None
            policy_warning_detail = None

            if timeout:
                failure_reason = FailureReason.TIMEOUT
            elif error:
                failure_reason = failure_reason or FailureReason.AGENT_CRASH
            elif failure_reason is None:
                forbidden_due_to_paths = not policy_result.forbidden_ok
                forbidden_due_to_allowlist = not policy_result.allowlist_ok and bool(policy_result.violating_paths)
                if forbidden_due_to_paths or forbidden_due_to_allowlist:
                    if solved_checks_passed and policy_result.allowed_support_paths and not policy_result.violating_paths:
                        policy_warning = "support_file_edits_allowed"
                        policy_warning_detail = f"allowed support edits: {', '.join(policy_result.allowed_support_paths)}"
                    else:
                        failure_reason = FailureReason.FORBIDDEN_EDIT
                elif solved_checks_passed and policy_result.allowed_support_paths and not policy_result.violating_paths:
                    policy_warning = "support_file_edits_allowed"
                    policy_warning_detail = f"allowed support edits: {', '.join(policy_result.allowed_support_paths)}"
            if failure_reason is None and not artifacts_ok:
                failure_reason = FailureReason.MISSING_ARTIFACT
                if artifact_failure_detail:
                    error = artifact_failure_detail

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
            first_pass_success = bool(success and (retries_after_failure or 0) == 0)
            recovered_after_failed_attempt = bool(success and (retries_after_failure or 0) > 0)
            expected_files_set = set(task.metadata.expected_files)
            touched_unexpected = bool(any(p not in expected_files_set for p in touched)) if expected_files_set else False
            expected_files_touched_count = sum(1 for p in touched if p in expected_files_set) if expected_files_set else 0

            detail = ""
            if failure_reason == FailureReason.MISSING_ARTIFACT and artifact_failure_detail:
                detail = f" detail={artifact_failure_detail}"
            elif failure_reason == FailureReason.FORBIDDEN_EDIT and policy_result.forbidden_reason_detail:
                detail = f" detail={policy_result.forbidden_reason_detail}"
            elif success and policy_warning_detail:
                detail = f" warning={policy_warning_detail}"
            self._log(
                f"result success={success} visible={int(visible_pass)} hidden={int(hidden_pass)} reason={(None if success else failure_reason.value if failure_reason else 'unknown')}{detail}"
            )
            return BenchmarkRunResult(
                benchmark_track=task.benchmark_track,
                task_id=task.id,
                task_version=task.task_version,
                task_family=task.family,
                task_difficulty=task.difficulty,
                task_language=task.language,
                task_source_type=task.source_type,
                task_tags=task.tags,
                task_type=task.metadata.task_type,
                benchmark_bucket=task.metadata.benchmark_bucket,
                runtime_stressors=task.metadata.runtime_stressors,
                expected_files=task.metadata.expected_files,
                task_checksum=task.task_checksum or "",
                agent_name=agent,
                adapter_name=adapter.name,
                adapter_version=adapter.version,
                adapter_capability=adapter.capability,
                fairness_classification=adapter.fairness_classification,
                fairness_notes=adapter.fairness_notes,
                telemetry_capability=adapter.telemetry_capability,
                model_name=model,
                provider_label=provider or ("openai" if base_url else None),
                success=success,
                pass_rate=float(success),
                failed=1 - success,
                timed_out=int(timeout),
                visible_pass=visible_pass,
                hidden_pass=hidden_pass,
                runtime_seconds=runtime_seconds,
                wall_clock_seconds=runtime_seconds,
                timeout=timeout,
                failure_reason=None if success else failure_reason,
                forbidden_reason_detail=(policy_result.forbidden_reason_detail if failure_reason == FailureReason.FORBIDDEN_EDIT else None),
                policy_warning=policy_warning,
                policy_warning_detail=policy_warning_detail,
                error=error,
                agent_exit_code=agent_exit_code,
                stderr_preview=agent_stderr_preview,
                touched_file_paths=touched,
                raw_touched_file_paths=raw_touched,
                meaningful_touched_paths=policy_result.meaningful_touched_paths,
                meaningful_expected_paths=policy_result.meaningful_expected_paths,
                meaningful_unexpected_paths=policy_result.meaningful_unexpected_paths,
                files_touched=files_touched,
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                num_shell_commands=num_shell_commands,
                num_failed_commands=num_failed_commands,
                tokens_input=None,
                tokens_output=None,
                total_tokens=None,
                estimated_cost=None,
                number_of_turns=number_of_turns,
                tool_calls_total=tool_calls_total,
                file_reads=file_reads,
                file_writes=file_writes,
                patch_attempts=patch_attempts,
                test_runs=test_runs,
                retries_after_failure=retries_after_failure,
                first_pass_success=first_pass_success,
                recovered_after_failed_attempt=recovered_after_failed_attempt,
                expected_files_touched_count=expected_files_touched_count,
                actual_files_touched_count=files_touched,
                touched_unexpected_files=touched_unexpected,
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

    def _check_required_artifacts(self, task: BenchmarkTask, touched: list[str]) -> tuple[bool, str | None]:
        expected = set(task.expected_artifacts)
        if "patch" in expected and not touched:
            return False, "missing required artifact: patch (no meaningful file changes detected)"
        if "test" in expected and not any(path.startswith("tests/") for path in touched):
            return False, "missing required artifact: test (no changes under tests/)"
        return True, None

    @staticmethod
    def _rmtree_onerror(func, path, _exc_info) -> None:
        path_obj = Path(path)
        os.chmod(path_obj, stat.S_IWRITE)
        func(path)

    @classmethod
    def _safe_rmtree(cls, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, onerror=cls._rmtree_onerror)

    @staticmethod
    def _copytree_ignore_runtime(_src: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            if name == "__pycache__" or name.endswith((".pyc", ".pyo")):
                ignored.add(name)
        return ignored

    def _run_repro_hidden(self, task: BenchmarkTask, workspace_repo: Path, timeout_seconds: int) -> tuple[bool, bool]:
        fixed_repo = task.task_dir / "hidden_checks" / "fixed_repo"
        if not fixed_repo.exists():
            return False, True
        temp_root = workspace_repo.parent / "fixed"
        self._safe_rmtree(temp_root)
        shutil.copytree(fixed_repo, temp_root, ignore=self._copytree_ignore_runtime)
        workspace_tests = workspace_repo / "tests"
        fixed_tests = temp_root / "tests"
        self._safe_rmtree(fixed_tests)
        if workspace_tests.exists():
            shutil.copytree(workspace_tests, fixed_tests, ignore=self._copytree_ignore_runtime)

        broken_pass, broken_outcomes, _, _ = run_commands(workspace_repo, task.hidden_verification, timeout_seconds)
        fixed_pass, fixed_outcomes, _, _ = run_commands(temp_root, task.hidden_verification, timeout_seconds)
        all_output = "\n".join(o.stdout + o.stderr for o in broken_outcomes + fixed_outcomes)
        syntax_noise = any(token in all_output for token in ["SyntaxError", "ImportError", "ModuleNotFoundError"])
        meaningful = any("assert" in (o.stdout + o.stderr).lower() or "failed" in (o.stdout + o.stderr).lower() for o in broken_outcomes)
        valid = (not broken_pass) and fixed_pass and meaningful and not syntax_noise
        return valid, not valid
