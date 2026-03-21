from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from villani_code.benchmark.agents import build_agent_runner
from villani_code.benchmark.diff_stats import build_patch_artifact_from_repo_diff, ensure_git_repo, line_stats, list_touched_files
from villani_code.benchmark.failure_taxonomy import classify_failure_taxonomy
from villani_code.benchmark.manifest import command_set_checksum, repo_checksum
from villani_code.benchmark.models import (
    BENCHMARK_VERSION,
    BenchmarkRunResult,
    BenchmarkTask,
    FieldQuality,
    FailureReason,
    ReproducibilityManifest,
    TaskFamily,
    TelemetryQuality,
    VerificationOutcome,
    classify_failure_mode,
)
from villani_code.benchmark.observability import derive_observability_metrics
from villani_code.benchmark.policy import (
    PATCH_ARTIFACT_PATH,
    PATH_CLASS_CLEARLY_UNRELATED,
    benchmark_asset_integrity,
    enforce_path_policy,
    filter_meaningful_touched_paths,
    normalize_path,
    unique_normalized_paths,
)
from villani_code.benchmark.prompt_contract import benchmark_contract_from_task, render_benchmark_prompt
from villani_code.benchmark.reporting import render_summary_table, summarize, write_markdown_report, write_results
from villani_code.benchmark.task_loader import load_tasks
from villani_code.benchmark.verifier import run_commands
from villani_code.benchmark.workspace import WorkspaceManager


@dataclass(frozen=True)
class RequiredArtifactCheck:
    artifact: str
    present: bool
    detail: str | None = None
    counts_as_patch_attempt_evidence: bool = False


class BenchmarkRunner:
    def __init__(self, output_dir: Path, keep_workspace: bool = False, private_suite_dir: Path | None = None) -> None:
        self.output_dir = output_dir
        self.workspace = WorkspaceManager(keep_workspace=keep_workspace)
        self.private_suite_dir = private_suite_dir

    @staticmethod
    def _log(message: str) -> None:
        print(f"[benchmark] {message}")

    @classmethod
    def _log_verification_outcomes(cls, stage: str, outcomes: list[VerificationOutcome]) -> None:
        if not outcomes:
            cls._log(f"{stage} verification had no commands")
            return
        for outcome in outcomes:
            runtime = outcome.finished_at - outcome.started_at
            status = "pass" if outcome.passed else "fail"
            detail = ""
            if not outcome.passed and outcome.stderr:
                detail = f" stderr={cls._stderr_snippet(outcome.stderr, max_len=180)}"
            cls._log(
                f"{stage} verify [{status}] code={outcome.exit_code} runtime={runtime:.2f}s cmd={outcome.command}{detail}"
            )
            if not outcome.passed and (outcome.stdout_artifact or outcome.stderr_artifact or outcome.metadata_artifact):
                cls._log(
                    f"{stage} verify artifacts stdout={outcome.stdout_artifact or '-'} stderr={outcome.stderr_artifact or '-'} meta={outcome.metadata_artifact or '-'}"
                )

    @classmethod
    def _log_event_metrics_summary(cls, metrics: dict[str, object]) -> None:
        cls._log(
            "agent telemetry "
            f"commands={metrics.get('num_shell_commands') or 0} "
            f"failed_commands={metrics.get('num_failed_commands') or 0} "
            f"tool_calls={metrics.get('tool_calls_total') or 0} "
            f"file_reads={metrics.get('file_reads') or 0} "
            f"file_writes={metrics.get('file_writes') or 0} "
            f"patches={metrics.get('patch_attempts') or 0} "
            f"denied_mutations={metrics.get('benchmark_mutation_denials') or 0} "
            f"test_runs={metrics.get('test_runs') or 0} "
            f"turns={metrics.get('number_of_turns') or 0}"
        )

    @staticmethod
    def _extract_termination_reason(events: list[object]) -> str | None:
        known_event_reasons = {
            "autonomous_completed",
            "villani_stop_decision",
            "benchmark_incomplete_no_patch",
            "benchmark_no_progress_after_forced_read",
            "benchmark_repeated_mutation_denials",
        }
        for event in reversed(events):
            payload = getattr(event, "payload", {})
            etype = str(getattr(event, "type", "") or "").strip()
            if isinstance(payload, dict):
                for key in ("termination_reason", "terminated_reason", "stop_reason", "done_reason", "reason"):
                    reason = payload.get(key)
                    if isinstance(reason, str) and reason.strip():
                        return reason.strip()
                payload_type = str(payload.get("type") or payload.get("event") or "").strip()
                if payload_type in known_event_reasons:
                    return payload_type
            if etype in known_event_reasons:
                return etype
        return None

    @staticmethod
    def _line_stats_for_task(repo_path: Path, *, require_patch_artifact: bool) -> tuple[int, int]:
        try:
            return line_stats(repo_path, require_patch_artifact=require_patch_artifact)
        except TypeError:
            return line_stats(repo_path)

    @staticmethod
    def _is_noop_patch_attempt(
        *,
        file_writes: int | None,
        patch_attempts: int | None,
        meaningful_changed_files: list[str],
        has_artifact_evidence: bool = False,
    ) -> bool:
        return (
            (file_writes or 0) == 0
            and (patch_attempts or 0) == 0
            and len(meaningful_changed_files) == 0
            and not has_artifact_evidence
        )

    @staticmethod
    def _required_artifact_path(artifact: str) -> str | None:
        normalized = normalize_path(artifact)
        if not normalized or normalized == "test":
            return None
        if normalized == "patch":
            return PATCH_ARTIFACT_PATH
        return normalized

    @staticmethod
    def _requires_patch_artifact(task: BenchmarkTask) -> bool:
        return "patch" in unique_normalized_paths(task.expected_artifacts)

    @classmethod
    def _maybe_generate_required_patch_artifact(cls, task: BenchmarkTask, repo_path: Path) -> bool:
        if not cls._requires_patch_artifact(task):
            return False

        patch_path = repo_path / PATCH_ARTIFACT_PATH
        if patch_path.exists():
            return False

        patch_text = build_patch_artifact_from_repo_diff(repo_path, require_patch_artifact=True)
        if not patch_text.strip():
            return False

        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch_text, encoding="utf-8")
        cls._log(
            f"benchmark_patch_artifact_generated path={PATCH_ARTIFACT_PATH} diff_nonempty=1 source=runner_generated"
        )
        return True

    @classmethod
    def _collect_required_artifact_checks(
        cls,
        task: BenchmarkTask,
        repo_path: Path,
        raw_touched: list[str],
    ) -> list[RequiredArtifactCheck]:
        checks: list[RequiredArtifactCheck] = []
        normalized_touched = unique_normalized_paths(raw_touched)
        existing_touched_test_files = [
            path for path in normalized_touched if path.startswith("tests/") and (repo_path / path).exists()
        ]

        for artifact in unique_normalized_paths(task.expected_artifacts):
            artifact_path = cls._required_artifact_path(artifact)
            if artifact == "test":
                if existing_touched_test_files:
                    checks.append(RequiredArtifactCheck(artifact=artifact, present=True))
                else:
                    checks.append(
                        RequiredArtifactCheck(
                            artifact=artifact,
                            present=False,
                            detail="missing required artifact: test (no touched test file exists under tests/)",
                        )
                    )
                continue

            if artifact_path is None:
                continue

            full_path = repo_path / artifact_path
            if not full_path.exists():
                checks.append(
                    RequiredArtifactCheck(
                        artifact=artifact,
                        present=False,
                        detail=f"missing required artifact: {artifact} ({artifact_path} does not exist)",
                    )
                )
                continue
            if full_path.is_file() and full_path.stat().st_size == 0:
                checks.append(
                    RequiredArtifactCheck(
                        artifact=artifact,
                        present=False,
                        detail=f"missing required artifact: {artifact} ({artifact_path} is empty)",
                    )
                )
                continue
            checks.append(
                RequiredArtifactCheck(
                    artifact=artifact,
                    present=True,
                    counts_as_patch_attempt_evidence=(artifact == "patch"),
                )
            )
        return checks


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
        tool_calls_total = 0
        file_reads = 0
        file_writes = 0
        patch_attempts = 0
        test_runs = 0
        number_of_turns = 0
        retries_after_failure = 0
        repair_attempts = 0
        noop_retries = 0
        empty_turn_retries = 0
        verification_failed = False
        verification_failed_then_recovered = False
        benchmark_mutation_denials = 0
        benchmark_write_denials = 0
        benchmark_patch_denials = 0
        first_denied_path: str | None = None
        first_denied_reason: str | None = None

        verification_commands = set(visible_commands + hidden_commands)
        expected_files_set = {str(path).replace("\\", "/").lstrip("./") for path in expected_files}
        pending_verification_commands: deque[bool] = deque()
        for e in events:
            etype = str(getattr(e, "type", "") or "")
            payload = getattr(e, "payload", {})
            if not isinstance(payload, dict):
                payload = {}

            if etype == "command_started":
                command_starts += 1
                command = str(payload.get("command", ""))
                is_verification_command = any(cmd in command for cmd in verification_commands)
                pending_verification_commands.append(is_verification_command)
                if is_verification_command:
                    test_runs += 1
            if etype == "command_finished":
                is_verification_command = pending_verification_commands.popleft() if pending_verification_commands else False
                exit_code = payload.get("exit_code")
                if isinstance(exit_code, int) and exit_code != 0:
                    command_failures += 1
                    if is_verification_command:
                        verification_failed = True
                elif isinstance(exit_code, int) and exit_code == 0 and is_verification_command and verification_failed:
                    retries_after_failure += 1
                    verification_failed = False
                    verification_failed_then_recovered = True
            if etype == "repair_attempt_started":
                repair_attempts += 1
            if etype == "benchmark_noop_completion_blocked":
                noop_retries += 1
            if etype == "empty_turn_retry":
                empty_turn_retries += 1

            payload_type = str(payload.get("type") or "").strip()
            payload_event = str(payload.get("event") or "").strip()
            event_type = payload_type or payload_event or etype or "runtime_event"
            tool_name = str(payload.get("name") or "").strip()
            ts = float(payload.get("ts", getattr(e, "timestamp", 0.0)))
            path = payload.get("path")

            if event_type in {"tool_call", "tool_invocation", "tool_result", "tool_use", "tool_started", "tool_finished"}:
                tool_calls_total += 1
            if event_type in {"model_message", "assistant_message", "turn_started", "turn_completed", "model_request_started", "first_text_delta"}:
                number_of_turns += 1
            if event_type in {"file_edit", "apply_patch", "write_file", "Write", "Patch"} or (
                event_type in {"tool_started", "tool_finished", "tool_result"} and tool_name in {"Write", "Patch"}
            ):
                file_writes += 1
                if event_type in {"apply_patch", "Patch"}:
                    patch_attempts += 1
                if first_edit is None:
                    first_edit = max(0.0, ts - started)
            if (
                event_type in {"file_read", "read_file", "open_file", "Read"}
                or (event_type in {"tool_started", "tool_finished", "tool_result"} and tool_name == "Read")
            ) and isinstance(path, str):
                normalized_path = str(path).replace("\\", "/").lstrip("./")
                file_reads += 1
                read_paths.add(normalized_path)
                if normalized_path in expected_files_set and first_expected_read is None:
                    first_expected_read = max(0.0, ts - started)

            if event_type == "benchmark_write_blocked":
                benchmark_mutation_denials += 1
                benchmark_write_denials += 1
            elif event_type == "benchmark_patch_blocked":
                benchmark_mutation_denials += 1
                benchmark_patch_denials += 1

            if event_type in {"benchmark_write_blocked", "benchmark_patch_blocked"}:
                paths = payload.get("paths")
                if first_denied_path is None and isinstance(paths, list):
                    for candidate in paths:
                        if isinstance(candidate, str) and candidate.strip():
                            first_denied_path = candidate.strip()
                            break
                if first_denied_reason is None:
                    reason = payload.get("reason")
                    if isinstance(reason, str) and reason.strip():
                        first_denied_reason = reason.strip()

        if number_of_turns == 0:
            number_of_turns = None
        return {
            "num_shell_commands": command_starts if command_starts > 0 else None,
            "num_failed_commands": command_failures if command_starts > 0 else None,
            "time_to_first_edit": first_edit,
            "expected_file_first_read_time": first_expected_read,
            "expected_files_read": read_paths & expected_files_set,
            "expected_files_total": len(expected_files),
            "tool_calls_total": tool_calls_total if tool_calls_total > 0 else None,
            "file_reads": file_reads if file_reads > 0 else None,
            "file_writes": file_writes if file_writes > 0 else None,
            "patch_attempts": patch_attempts if patch_attempts > 0 else None,
            "test_runs": test_runs if test_runs > 0 else None,
            "number_of_turns": number_of_turns,
            "retries_after_failure": retries_after_failure if retries_after_failure > 0 else 0,
            "retry_count": retries_after_failure + repair_attempts + noop_retries + empty_turn_retries,
            "verification_failed_then_recovered": verification_failed_then_recovered,
            "benchmark_mutation_denials": benchmark_mutation_denials if benchmark_mutation_denials > 0 else None,
            "benchmark_write_denials": benchmark_write_denials if benchmark_write_denials > 0 else None,
            "benchmark_patch_denials": benchmark_patch_denials if benchmark_patch_denials > 0 else None,
            "first_denied_path": first_denied_path,
            "first_denied_reason": first_denied_reason,
        }

    @staticmethod
    def _extract_token_usage(
        execution,
        field_quality_map: dict[str, FieldQuality],
    ) -> tuple[int | None, int | None, int | None, FieldQuality]:
        def _coerce_int(value: object) -> int | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return None

        def _usage_from_payload(payload: dict[str, object]) -> tuple[int | None, int | None, int | None]:
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                return None, None, None
            tokens_in = next(
                (
                    value
                    for value in (
                        _coerce_int(usage.get("input_tokens")),
                        _coerce_int(usage.get("prompt_tokens")),
                        _coerce_int(usage.get("prompt_token_count")),
                    )
                    if value is not None
                ),
                None,
            )
            tokens_out = next(
                (
                    value
                    for value in (
                        _coerce_int(usage.get("output_tokens")),
                        _coerce_int(usage.get("completion_tokens")),
                        _coerce_int(usage.get("completion_token_count")),
                    )
                    if value is not None
                ),
                None,
            )
            total = next(
                (
                    value
                    for value in (
                        _coerce_int(usage.get("total_tokens")),
                        _coerce_int(payload.get("total_tokens")),
                    )
                    if value is not None
                ),
                None,
            )
            if total is None and tokens_in is not None and tokens_out is not None:
                total = tokens_in + tokens_out
            return tokens_in, tokens_out, total

        direct_in = _coerce_int(getattr(execution, "tokens_input", None))
        direct_out = _coerce_int(getattr(execution, "tokens_output", None))
        direct_total = _coerce_int(getattr(execution, "total_tokens", None))
        if direct_in is not None or direct_out is not None or direct_total is not None:
            if direct_total is None and direct_in is not None and direct_out is not None:
                direct_total = direct_in + direct_out
            return direct_in, direct_out, direct_total, FieldQuality.EXACT

        events = getattr(execution, "events", [])
        sum_in = 0
        sum_out = 0
        sum_total = 0
        saw_in = False
        saw_out = False
        saw_total = False
        for event in events:
            payload = getattr(event, "payload", {})
            if not isinstance(payload, dict):
                continue
            tokens_in, tokens_out, total = _usage_from_payload(payload)
            if tokens_in is not None:
                sum_in += tokens_in
                saw_in = True
            if tokens_out is not None:
                sum_out += tokens_out
                saw_out = True
            if total is not None:
                sum_total += total
                saw_total = True

        latest_in = sum_in if saw_in else None
        latest_out = sum_out if saw_out else None
        latest_total = sum_total if saw_total else None
        if latest_in is not None or latest_out is not None or latest_total is not None:
            if latest_total is None and latest_in is not None and latest_out is not None:
                latest_total = latest_in + latest_out
            return latest_in, latest_out, latest_total, FieldQuality.EXACT

        existing_quality = field_quality_map.get("tokens_input")
        if existing_quality in {FieldQuality.EXACT, FieldQuality.INFERRED}:
            return None, None, None, existing_quality
        return None, None, None, FieldQuality.UNAVAILABLE

    @staticmethod
    def _tokenize_command(command: str) -> set[str]:
        return {token.strip("\'\" ") for token in command.replace("=", " ").split() if token.strip("\'\" ")}

    @classmethod
    def _verification_relevant(cls, task: BenchmarkTask, executed_commands: list[str], touched: list[str]) -> bool:
        if not executed_commands:
            return False
        touched_set = {str(p).replace("\\", "/").lstrip("./") for p in touched}
        expected_set = {str(p).replace("\\", "/").lstrip("./") for p in task.metadata.expected_files}
        allowlisted = {str(p).replace("\\", "/").lstrip("./") for p in (task.allowlist_paths + task.allowed_paths)}
        narrow_task = bool(expected_set) or task.max_files_touched <= 3

        def _module_for(path: str) -> str | None:
            if not path.endswith('.py'):
                return None
            return Path(path).with_suffix('').as_posix().replace('/', '.')

        for command in executed_commands:
            cmd = command.replace("\\", "/")
            tokens = cls._tokenize_command(command)
            if any(path in cmd or path in tokens or Path(path).name in tokens for path in touched_set):
                return True
            if any(path in cmd or path in tokens or Path(path).name in tokens for path in expected_set):
                return True
            if any(path.startswith('tests/') and (path in cmd or Path(path).name in tokens) for path in touched_set):
                return True
            modules = {m for m in [_module_for(p) for p in (touched_set | expected_set)] if m}
            if any((f'-m {module}' in cmd) or (module in tokens) for module in modules):
                return True
            if narrow_task and any(scope and (scope in cmd or scope in tokens) for scope in allowlisted):
                return True
            if narrow_task and ('pytest -q' in cmd or cmd.strip() == 'pytest'):
                continue
        return False

    @staticmethod
    def _stderr_snippet(stderr: str, max_len: int = 240) -> str:
        compact = " ".join(stderr.strip().split())
        if len(compact) <= max_len:
            return compact
        return compact[: max_len - 3] + "..."

    @staticmethod
    def _recovery_attempted(
        retries_after_failure: int | None,
        verification_attempt_count: int,
        had_failed_verification: bool,
        failure_reason: FailureReason | None,
    ) -> bool:
        if (retries_after_failure or 0) > 0:
            return True
        return bool(
            verification_attempt_count > 1
            and (
                had_failed_verification
                or failure_reason in {FailureReason.VISIBLE_VERIFICATION_FAILED, FailureReason.HIDDEN_VERIFICATION_FAILED}
            )
        )

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
        visible_outcomes: list[VerificationOutcome] = []
        hidden_outcomes: list[VerificationOutcome] = []
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
        retry_count: int | None = None
        tokens_input: int | None = None
        tokens_output: int | None = None
        total_tokens: int | None = None
        agent_exit_code: int | None = None
        agent_stderr_preview: str | None = None
        denied_summary_detail = ""
        prompt_artifact_path: str | None = None
        contract_artifact_path: str | None = None
        metrics: dict[str, object] = {
            "expected_files_read": set(),
            "expected_files_total": len(task.metadata.expected_files),
            "verification_failed_then_recovered": False,
            "retry_count": None,
        }

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
                # Fairness map: benchmark runs now use one canonical prompt contract for all agents,
                # and do not provide Villani-only benchmark runtime controls.
                benchmark_prompt = render_benchmark_prompt(task, workspace_repo)
                task_debug_dir = self.output_dir / "agent_debug" / f"{task.id}__r{repeat_index}"
                task_debug_dir.mkdir(parents=True, exist_ok=True)
                contract_artifact = task_debug_dir / "rendered_prompt.txt"
                contract_artifact.write_text(benchmark_prompt, encoding="utf-8")
                contract_artifact_path = str(contract_artifact)

                render_launch_prompt = getattr(adapter, "render_launch_prompt", lambda value: value)
                launch_prompt = render_launch_prompt(benchmark_prompt)
                launch_artifact = task_debug_dir / "rendered_launch_prompt.txt"
                launch_artifact.write_text(launch_prompt, encoding="utf-8")
                prompt_artifact_path = str(launch_artifact)

                contract = benchmark_contract_from_task(task, workspace_repo)
                prompt_meta = {
                    "task_id": task.id,
                    "agent": agent,
                    "contract_prompt_path": contract_artifact_path,
                    "launch_prompt_path": prompt_artifact_path,
                    "launch_prompt_differs": launch_prompt != benchmark_prompt,
                    "contract_marker": "Benchmark task contract (shared across all agents):",
                    "contract": contract.model_dump(),
                    "scoring_inputs_mode": "harness_only",
                }
                (task_debug_dir / "rendered_prompt_meta.json").write_text(
                    json.dumps(prompt_meta, indent=2),
                    encoding="utf-8",
                )

                execution = adapter.run_agent(
                    repo_path=workspace_repo,
                    prompt=benchmark_prompt,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    provider=provider,
                    timeout=timeout_seconds,
                    benchmark_config_json=None,
                    debug_dir=task_debug_dir,
                )
                timeout = execution.timeout
                telemetry_quality = execution.telemetry_quality
                field_quality_map = execution.telemetry_field_quality_map
                tokens_input, tokens_output, total_tokens, token_quality = self._extract_token_usage(execution, field_quality_map)
                field_quality_map = dict(field_quality_map)
                field_quality_map["tokens_input"] = token_quality
                field_quality_map["tokens_output"] = token_quality
                field_quality_map["total_tokens"] = token_quality
                execution_retry_count = getattr(execution, "retry_count", None)
                if isinstance(execution_retry_count, int):
                    retry_count = execution_retry_count
                    field_quality_map["retry_count"] = FieldQuality.EXACT
                agent_exit_code = execution.exit_code
                agent_stderr_preview = self._stderr_snippet(execution.stderr, max_len=400) if execution.stderr else None
                self._log(
                    f"agent exit_code={execution.exit_code} runtime={execution.runtime_seconds:.1f}s, running verification..."
                )
                if execution.debug_artifacts:
                    self._log(
                        "agent debug artifacts "
                        f"stdout={execution.debug_artifacts.get('agent_stdout', '-')} "
                        f"stderr={execution.debug_artifacts.get('agent_stderr', '-')}"
                    )

                if not execution.timeout and execution.exit_code not in {None, 0}:
                    error = f"agent process exited with code {execution.exit_code}"
                    if agent_stderr_preview:
                        error = f"{error}; stderr: {agent_stderr_preview}"
                    failure_reason = FailureReason.AGENT_CRASH
                    self._log(f"agent crash: {agent_stderr_preview or 'no stderr preview available'}")

                metrics = self._event_metrics(execution.events, started, task.metadata.expected_files, task.visible_verification, task.hidden_verification)
                self._log_event_metrics_summary(metrics)
                denied_count = int(metrics.get("benchmark_mutation_denials") or 0)
                denied_first_path = metrics.get("first_denied_path")
                denied_first_reason = metrics.get("first_denied_reason")
                denied_summary_detail = ""
                if denied_count > 0:
                    denied_summary_detail = (
                        f"count={denied_count} first_path={denied_first_path or '-'} first_reason={denied_first_reason or '-'}"
                    )
                    self._log(f"benchmark mutation denials {denied_summary_detail}")
                if field_quality_map.get("num_shell_commands") in {FieldQuality.EXACT, FieldQuality.INFERRED}:
                    num_shell_commands = metrics["num_shell_commands"]
                    num_failed_commands = metrics["num_failed_commands"]
                    time_to_first_edit = metrics["time_to_first_edit"]
                    expected_file_first_read_time = metrics["expected_file_first_read_time"]
                    expected_files_total = metrics["expected_files_total"]
                    number_of_turns = metrics["number_of_turns"]
                    tool_calls_total = metrics["tool_calls_total"]
                    file_reads = metrics["file_reads"]
                    file_writes = metrics["file_writes"]
                    patch_attempts = metrics["patch_attempts"]
                    test_runs = metrics["test_runs"]
                    retries_after_failure = metrics["retries_after_failure"]
                    retry_count = metrics["retry_count"]
                elif field_quality_map.get("retry_count") in {FieldQuality.EXACT, FieldQuality.INFERRED}:
                    retry_count = metrics["retry_count"]

                self._maybe_generate_required_patch_artifact(task, workspace_repo)
                raw_post_run_changes = list_touched_files(workspace_repo)
                post_run_changes = filter_meaningful_touched_paths(
                    raw_post_run_changes,
                    require_patch_artifact=self._requires_patch_artifact(task),
                )
                post_run_artifact_checks = self._collect_required_artifact_checks(task, workspace_repo, raw_post_run_changes)
                changed_files_for_log = post_run_changes
                termination_reason = self._extract_termination_reason(execution.events)
                noop_candidate = self._is_noop_patch_attempt(
                    file_writes=metrics["file_writes"],
                    patch_attempts=metrics["patch_attempts"],
                    meaningful_changed_files=post_run_changes,
                    has_artifact_evidence=any(
                        check.counts_as_patch_attempt_evidence and check.present for check in post_run_artifact_checks
                    ),
                )
                self._log(f"termination_reason={termination_reason or 'unknown'}")
                if changed_files_for_log:
                    self._log(f"meaningful_repo_changes=yes changed_files={', '.join(changed_files_for_log)}")
                else:
                    self._log("meaningful_repo_changes=no changed_files=none")
                self._log(f"no_op_candidate={int(noop_candidate)}")
                if noop_candidate:
                    self._log("no-op detected: no meaningful patch attempt")
                    if execution.stdout.strip() or execution.stderr.strip():
                        stdout_preview = self._stderr_snippet(execution.stdout, max_len=180) if execution.stdout.strip() else ""
                        stderr_preview = self._stderr_snippet(execution.stderr, max_len=180) if execution.stderr.strip() else ""
                        self._log(
                            "no-op output preview "
                            f"stdout={stdout_preview or '-'} "
                            f"stderr={stderr_preview or '-'}"
                        )

                if error is None:
                    self._log(f"running visible verification commands ({len(task.visible_verification)})")
                    visible_pass, visible_outcomes, first_verify, l_verify, visible_launch_failed = run_commands(
                        workspace_repo,
                        task.visible_verification,
                        timeout_seconds,
                        stage="visible",
                        logger=self._log,
                        artifact_dir=task_debug_dir,
                    )
                    self._log_verification_outcomes("visible", visible_outcomes)
                    if first_verify:
                        time_to_first_verify = first_verify - started
                    last_verify = (l_verify - started) if l_verify else None
                    verifications.extend(item.command for item in visible_outcomes)
                    if not visible_pass:
                        failure_reason = (
                            FailureReason.VERIFICATION_COMMAND_FAILED_TO_LAUNCH
                            if visible_launch_failed
                            else FailureReason.VISIBLE_VERIFICATION_FAILED
                        )

                    if task.family == TaskFamily.REPRO_TEST:
                        self._log("running hidden repro validation")
                        hidden_pass, invalid_repro = self._run_repro_hidden(task, workspace_repo, timeout_seconds)
                        self._log(f"hidden repro validation result pass={int(hidden_pass)} invalid_repro={int(invalid_repro)}")
                        if not hidden_pass:
                            failure_reason = FailureReason.INVALID_REPRO_TEST if invalid_repro else FailureReason.HIDDEN_VERIFICATION_FAILED
                    else:
                        hidden_commands = task.hidden_verification
                        self._log(f"running hidden verification commands ({len(hidden_commands)})")
                        hidden_pass, hidden_outcomes, _, l_verify_hidden, hidden_launch_failed = run_commands(
                            workspace_repo,
                            hidden_commands,
                            timeout_seconds,
                            stage="hidden",
                            logger=self._log,
                            artifact_dir=task_debug_dir,
                        )
                        self._log_verification_outcomes("hidden", hidden_outcomes)
                        verifications.extend(item.command for item in hidden_outcomes)
                        if l_verify_hidden:
                            last_verify = l_verify_hidden - started
                        if visible_pass and not hidden_pass and failure_reason is None:
                            failure_reason = (
                                FailureReason.VERIFICATION_COMMAND_FAILED_TO_LAUNCH
                                if hidden_launch_failed
                                else FailureReason.HIDDEN_VERIFICATION_FAILED
                            )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                failure_reason = FailureReason.BENCHMARK_ERROR
                self._log(f"benchmark harness error: {self._stderr_snippet(error, max_len=300)}")

            self._maybe_generate_required_patch_artifact(task, workspace_repo)
            raw_touched = list_touched_files(workspace_repo)
            policy_result = enforce_path_policy(
                raw_touched,
                task.allowlist_paths,
                task.forbidden_paths,
                expected_paths=task.metadata.expected_files,
                family=task.family.value,
                task_type=task.task_type or task.metadata.task_type,
                allowed_support_files=task.metadata.allowed_support_files,
                allowed_support_globs=task.metadata.allowed_support_globs,
                require_patch_artifact=self._requires_patch_artifact(task),
            )
            touched = policy_result.meaningful_touched_paths
            files_touched = len(touched)
            lines_added, lines_deleted = self._line_stats_for_task(
                workspace_repo,
                require_patch_artifact=self._requires_patch_artifact(task),
            )
            runtime_seconds = time.monotonic() - started
            artifact_checks = self._collect_required_artifact_checks(task, workspace_repo, raw_touched)
            artifacts_ok, artifact_failure_detail = self._check_required_artifacts(artifact_checks)

            solved_checks_passed = visible_pass and hidden_pass
            hidden_required = task.success_policy.require_hidden_pass or bool(task.hidden_verification)
            policy_warning = None
            policy_warning_detail = None

            if timeout:
                failure_reason = FailureReason.TIMEOUT
            elif task.inspect_only and files_touched > 0:
                failure_reason = FailureReason.INSPECT_ONLY_VIOLATION
            elif error:
                failure_reason = failure_reason or FailureReason.AGENT_CRASH
            elif failure_reason is None:
                if solved_checks_passed:
                    if policy_result.violating_paths:
                        failure_reason = FailureReason.FORBIDDEN_EDIT
                    elif policy_result.metadata_omission_paths:
                        policy_warning = "metadata_omission_reasonable"
                        policy_warning_detail = f"allowed task-related edit: {', '.join(policy_result.metadata_omission_paths)}"
                    elif policy_result.allowed_support_paths:
                        policy_warning = "support_file_edits"
                        policy_warning_detail = f"allowed support edits: {', '.join(policy_result.allowed_support_paths)}"
                elif not visible_pass or not hidden_pass:
                    if policy_result.violating_paths and failure_reason not in {FailureReason.VISIBLE_VERIFICATION_FAILED, FailureReason.HIDDEN_VERIFICATION_FAILED}:
                        failure_reason = FailureReason.FORBIDDEN_EDIT
            if failure_reason is None and not artifacts_ok:
                failure_reason = FailureReason.MISSING_ARTIFACT
                if artifact_failure_detail:
                    error = artifact_failure_detail

            no_op_patch_attempt = self._is_noop_patch_attempt(
                file_writes=None,
                patch_attempts=None,
                meaningful_changed_files=touched,
                has_artifact_evidence=any(
                    check.counts_as_patch_attempt_evidence and check.present for check in artifact_checks
                ),
            )
            if (
                no_op_patch_attempt
                and not timeout
                and not error
                and not policy_result.violating_paths
                and failure_reason in {None, FailureReason.VISIBLE_VERIFICATION_FAILED, FailureReason.HIDDEN_VERIFICATION_FAILED, FailureReason.MISSING_ARTIFACT}
            ):
                failure_reason = FailureReason.BENCHMARK_NO_PATCH_ATTEMPT

            policy_repo_clean_ok = policy_result.allowlist_ok and policy_result.forbidden_ok
            if solved_checks_passed and not policy_result.violating_paths:
                policy_repo_clean_ok = True

            success = int(
                (not timeout or not task.success_policy.fail_on_timeout)
                and (visible_pass or not task.success_policy.require_visible_pass)
                and (hidden_pass or not hidden_required)
                and (policy_repo_clean_ok or not task.success_policy.fail_on_repo_dirty_outside_allowlist)
                and files_touched <= (task.expected_touched_max if task.expected_touched_max is not None else task.max_files_touched)
                and artifacts_ok
                and error is None
                and failure_reason is None
            )
            manifest.telemetry_quality = telemetry_quality
            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

            hidden_checks = task.hidden_verification
            hidden_failed = any("hidden" in c for c in verifications[-len(hidden_checks) :]) if hidden_checks else False
            expected_files_set = set(policy_result.meaningful_expected_paths)
            touched_unexpected = bool(policy_result.meaningful_unexpected_paths)
            observability = derive_observability_metrics(
                success=bool(success),
                expected_files=task.metadata.expected_files,
                expected_files_read=set(metrics.get("expected_files_read") or set()),
                expected_files_touched=expected_files_set,
                verification_failed_then_recovered=bool(metrics.get("verification_failed_then_recovered")),
                retries_after_failure=retries_after_failure,
            )
            expected_files_found = observability.expected_files_found
            expected_files_total = observability.expected_files_total
            expected_files_touched_count = observability.expected_files_touched_count
            first_pass_success = observability.first_pass_success
            recovered_after_failed_attempt = observability.recovered_after_failed_attempt
            visible_only_pass = bool(visible_pass and not hidden_pass)
            unrelated_file_touch = any(cls == PATH_CLASS_CLEARLY_UNRELATED for cls in policy_result.path_classifications.values())
            verification_relevant = self._verification_relevant(task, verifications, touched)
            recovery_attempted = observability.recovery_attempted
            recovery_success = observability.recovery_success
            no_progress_termination = failure_reason == FailureReason.NO_PROGRESS
            failure_mode_category = classify_failure_mode(success=success, failure_reason=failure_reason)
            verification_output = "\n".join(
                part
                for outcome in [*visible_outcomes, *hidden_outcomes]
                for part in (outcome.stdout, outcome.stderr)
                if part
            )
            failure_taxonomy, failure_taxonomy_detail = classify_failure_taxonomy(
                success=success,
                failure_reason=failure_reason,
                visible_pass=visible_pass,
                hidden_pass=hidden_pass,
                verification_output=verification_output,
                error=error,
                stderr_preview=agent_stderr_preview,
                num_shell_commands=num_shell_commands,
                file_reads=file_reads,
                patch_attempts=patch_attempts,
                files_touched=files_touched,
                meaningful_touched_paths=policy_result.meaningful_touched_paths,
                meaningful_expected_paths=policy_result.meaningful_expected_paths,
                meaningful_unexpected_paths=policy_result.meaningful_unexpected_paths,
                touched_file_paths=touched,
                expected_files=task.metadata.expected_files,
                touched_unexpected_files=touched_unexpected,
                unrelated_file_touch=unrelated_file_touch,
                verification_relevant=verification_relevant,
                task_family=task.family,
                task_type=task.task_type or task.metadata.task_type,
                benchmark_category=task.benchmark_category or task.metadata.benchmark_category,
            )
            field_quality_map.setdefault("retry_count", FieldQuality.INFERRED if retry_count is not None else FieldQuality.UNAVAILABLE)
            field_quality_map["failure_mode_category"] = FieldQuality.EXACT
            field_quality_map["failure_taxonomy"] = FieldQuality.EXACT

            detail = ""
            if failure_reason == FailureReason.MISSING_ARTIFACT and artifact_failure_detail:
                detail = f" detail={artifact_failure_detail}"
            elif failure_reason == FailureReason.FORBIDDEN_EDIT:
                if policy_result.meaningful_unexpected_paths:
                    detail = (
                        " detail="
                        f"{policy_result.forbidden_reason_detail or 'clearly unrelated meaningful edits'}"
                        f"; meaningful_unexpected_paths={', '.join(policy_result.meaningful_unexpected_paths)}"
                    )
                elif policy_result.forbidden_reason_detail:
                    detail = f" detail={policy_result.forbidden_reason_detail}"
            elif success and policy_warning_detail:
                detail = f" warning={policy_warning} detail={policy_warning_detail}"
            if denied_summary_detail:
                if policy_warning is None:
                    policy_warning = "benchmark_mutation_denials"
                    policy_warning_detail = denied_summary_detail
                elif policy_warning_detail:
                    policy_warning_detail = f"{policy_warning_detail}; {denied_summary_detail}"
                else:
                    policy_warning_detail = denied_summary_detail
            self._log(
                f"result success={success} visible={int(visible_pass)} hidden={int(hidden_pass)} reason={(None if success else failure_reason.value if failure_reason else 'unknown')}{detail}"
            )
            return BenchmarkRunResult(
                benchmark_track=task.benchmark_track,
                task_id=task.id,
                task_version=task.task_version,
                benchmark_category=task.benchmark_category or task.metadata.benchmark_category,
                task_family=task.family,
                task_difficulty=task.difficulty,
                task_language=task.language,
                task_source_type=task.source_type,
                task_tags=task.tags,
                task_type=task.task_type or task.metadata.task_type,
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
                visible_only_pass=visible_only_pass,
                runtime_seconds=runtime_seconds,
                wall_clock_seconds=runtime_seconds,
                timeout=timeout,
                failure_reason=None if success else failure_reason,
                forbidden_reason_detail=(policy_result.forbidden_reason_detail if failure_reason in {FailureReason.FORBIDDEN_EDIT, FailureReason.INSPECT_ONLY_VIOLATION} else None),
                policy_warning=policy_warning,
                policy_warning_detail=policy_warning_detail,
                error=error,
                agent_exit_code=agent_exit_code,
                stderr_preview=agent_stderr_preview,
                touched_file_paths=touched,
                raw_touched_file_paths=raw_touched,
                normalized_touched_paths=policy_result.normalized_touched_paths,
                runtime_artifact_paths=policy_result.ignored_junk_paths,
                path_classifications=policy_result.path_classifications,
                meaningful_touched_paths=policy_result.meaningful_touched_paths,
                meaningful_expected_paths=policy_result.meaningful_expected_paths,
                meaningful_unexpected_paths=policy_result.meaningful_unexpected_paths,
                files_touched=files_touched,
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                num_shell_commands=num_shell_commands,
                num_failed_commands=num_failed_commands,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                total_tokens=total_tokens,
                retry_count=retry_count,
                failure_mode_category=failure_mode_category,
                failure_taxonomy=failure_taxonomy,
                failure_taxonomy_detail=failure_taxonomy_detail,
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
                unrelated_file_touch=unrelated_file_touch,
                verification_relevant=verification_relevant,
                recovery_attempted=recovery_attempted,
                recovery_success=recovery_success,
                no_progress_termination=no_progress_termination,
                verifications_run=verifications,
                verification_attempt_count=len(verifications),
                time_to_first_edit=time_to_first_edit,
                time_to_first_verify=time_to_first_verify,
                last_verification_time=last_verify,
                expected_files_found=expected_files_found,
                expected_files_total=expected_files_total,
                expected_file_first_read_time=expected_file_first_read_time,
                self_corrected_after_failed_verify=observability.self_corrected_after_failed_verify,
                touched_irrelevant_files=sum(1 for p in touched if not any(p.startswith(a) for a in task.allowlist_paths)),
                telemetry_quality=telemetry_quality,
                telemetry_field_quality_map=field_quality_map,
                workspace_preserved=self.workspace.keep_workspace,
                reproducibility_manifest_path=str(manifest_path),
                prompt_artifact_path=prompt_artifact_path,
                contract_artifact_path=contract_artifact_path,
                scoring_inputs_mode="harness_only",
                repeat_index=repeat_index,
            )

    def _check_required_artifacts(
        self,
        artifact_checks: list[RequiredArtifactCheck],
    ) -> tuple[bool, str | None]:
        for check in artifact_checks:
            if not check.present:
                return False, check.detail
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

        broken_pass, broken_outcomes, _, _, _ = run_commands(workspace_repo, task.hidden_verification, timeout_seconds, stage="hidden(repro-broken)", logger=self._log)
        self._log_verification_outcomes("hidden(repro-broken)", broken_outcomes)
        fixed_pass, fixed_outcomes, _, _, _ = run_commands(temp_root, task.hidden_verification, timeout_seconds, stage="hidden(repro-fixed)", logger=self._log)
        self._log_verification_outcomes("hidden(repro-fixed)", fixed_outcomes)
        all_output = "\n".join(o.stdout + o.stderr for o in broken_outcomes + fixed_outcomes)
        syntax_noise = any(token in all_output for token in ["SyntaxError", "ImportError", "ModuleNotFoundError"])
        meaningful = any("assert" in (o.stdout + o.stderr).lower() or "failed" in (o.stdout + o.stderr).lower() for o in broken_outcomes)
        valid = (not broken_pass) and fixed_pass and meaningful and not syntax_noise
        return valid, not valid
