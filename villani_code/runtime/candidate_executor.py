from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.benchmark.policy import enforce_path_policy
from villani_code.patch_apply import PatchApplyError, apply_unified_diff, parse_unified_diff
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.runtime.policy import WeakSearchPolicyProfile, is_direct_repair_profile
from villani_code.runtime.workspace import cleanup_candidate_workspace, prepare_candidate_workspace
from villani_code.synthesize.diff_guard import guard_candidate_diff
from villani_code.synthesize.edit_budget import EditBudget
from villani_code.tools import tool_specs
from villani_code.utils import normalize_content_blocks
from villani_code.verify.runner import run_staged_verifier


@dataclass(slots=True)
class CandidateExecutionResult:
    changed_files: list[str] = field(default_factory=list)
    diff_stats: dict[str, Any] = field(default_factory=dict)
    patch_artifact_path: str = ""
    diff_text: str = ""
    verification_outputs: dict[str, Any] = field(default_factory=dict)
    hard_fail: bool = False
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    failure_signature: str = ""
    blocked_reason: str = ""
    attempt_summary: str = ""
    attempt_category: str = "verification_failed"
    success: bool = False
    prompt_summary: str = ""
    target_verification_passed: bool = False
    collateral_verification_passed: bool = False
    static_sanity_passed: bool = False
    minimality_score: float = 0.0
    novelty_score: float = 0.0
    verification_stage: str = "stage_0"
    target_exit_codes: list[int] = field(default_factory=list)
    target_command_count: int = 0
    workspace_prep_seconds: float = 0.0
    prompt_build_seconds: float = 0.0
    model_execution_seconds: float = 0.0
    tool_execution_seconds: float = 0.0
    verification_seconds: float = 0.0
    candidate_total_seconds: float = 0.0
    workspace_strategy: str = ""
    policy_profile: str = ""
    direct_repair_attempted: bool = False
    direct_repair_suspect: str = ""
    session_context_reused: bool = False
    escalation_occurred: bool = False
    escalation_reason: str = ""
    hypothesis_stage_skipped_initially: bool = False
    prompt_tokens_first_attempt: int = 0
    tool_calls_first_attempt: int = 0
    exploration_block_triggered: bool = False
    apply_mode: str = "none"
    apply_failure_reason: str = ""
    meaningful_patch_produced: bool = False


@dataclass(slots=True)
class WeakSearchSessionContext:
    planning_prompt: str
    planning_initialized: bool = False
    plan_invalidated: bool = False


class CandidateExecutor:
    def __init__(self, runner: Any, instruction: str, max_patch_lines: int, max_files_per_patch: int) -> None:
        self.runner = runner
        self.instruction = instruction
        self.edit_budget = EditBudget(max_files=max_files_per_patch, max_lines=max_patch_lines)
        self._last_tool_execution_seconds = 0.0
        self._last_assistant_completion_text = ""
        self._last_tool_calls = 0
        self._last_exploration_block_triggered = False


    def evaluate_direct_patch(
        self,
        *,
        repo_path: Path,
        objective: str,
        target_file: str,
        target_file_contents: str,
        failing_test_file: str = "",
        failing_test_contents: str = "",
        verification_target: str = "",
        constraints: dict[str, Any],
        benchmark_config: Any,
        attempt_id: str,
        timeout_budget_seconds: float,
    ) -> CandidateExecutionResult:
        return self._evaluate_patch_attempt(
            repo_path=repo_path,
            objective=objective,
            target_file=target_file,
            target_file_contents=target_file_contents,
            failing_test_file=failing_test_file,
            failing_test_contents=failing_test_contents,
            verification_target=verification_target,
            constraints=constraints,
            benchmark_config=benchmark_config,
            attempt_id=attempt_id,
            timeout_budget_seconds=timeout_budget_seconds,
            stage_name="stage1",
            retry_hint="",
        )

    def _evaluate_patch_attempt(
        self,
        *,
        repo_path: Path,
        objective: str,
        target_file: str,
        target_file_contents: str,
        failing_test_file: str,
        failing_test_contents: str,
        verification_target: str,
        constraints: dict[str, Any],
        benchmark_config: Any,
        attempt_id: str,
        timeout_budget_seconds: float,
        stage_name: str = "stage1",
        retry_hint: str = "",
    ) -> CandidateExecutionResult:
        started = time.monotonic()
        handle = prepare_candidate_workspace(repo_path, fast_path=True)
        workspace = handle.workspace
        prompt_started = time.monotonic()
        prompt = self._build_direct_diff_prompt(
            objective=objective,
            target_file=target_file,
            target_file_contents=target_file_contents,
            failing_test_file=failing_test_file,
            failing_test_contents=failing_test_contents,
            verification_target=verification_target,
            stage_name=stage_name,
            retry_hint=retry_hint,
        )
        prompt_build_seconds = time.monotonic() - prompt_started
        prompt_tokens = len(prompt.split())
        model_started = time.monotonic()
        try:
            diff_text = self._request_patch_output(prompt, stage_name=stage_name)
        except Exception as exc:  # noqa: BLE001
            cleanup_candidate_workspace(handle)
            return CandidateExecutionResult(
                hard_fail=True,
                blocked_reason="blocked_model_failure",
                attempt_summary=str(exc),
                attempt_category="blocked_model_failure",
                verification_stage="stage_0",
                workspace_prep_seconds=handle.prep_seconds,
                prompt_build_seconds=prompt_build_seconds,
                model_execution_seconds=time.monotonic() - model_started,
                candidate_total_seconds=time.monotonic() - started,
                workspace_strategy=handle.strategy,
                policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
                direct_repair_attempted=True,
                direct_repair_suspect=target_file,
                prompt_tokens_first_attempt=prompt_tokens,
            )
        model_execution_seconds = time.monotonic() - model_started
        before_map = self._read_repo_text_map(workspace)
        apply_mode = "none"
        apply_failure_reason = ""
        try:
            apply_mode, apply_failure_reason = self._apply_patch_pipeline(
                workspace=workspace,
                target_file=target_file,
                target_file_contents=target_file_contents,
                patch_text=diff_text,
            )
        except Exception as exc:  # noqa: BLE001
            cleanup_candidate_workspace(handle)
            return CandidateExecutionResult(
                hard_fail=True,
                blocked_reason="blocked_runtime_error",
                attempt_summary=f"patch_apply_failed:{exc}",
                attempt_category="blocked_runtime_error",
                verification_stage="stage_0",
                workspace_prep_seconds=handle.prep_seconds,
                prompt_build_seconds=prompt_build_seconds,
                model_execution_seconds=model_execution_seconds,
                candidate_total_seconds=time.monotonic() - started,
                workspace_strategy=handle.strategy,
                policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
                direct_repair_attempted=True,
                direct_repair_suspect=target_file,
                prompt_tokens_first_attempt=prompt_tokens,
                apply_mode=apply_mode,
                apply_failure_reason=apply_failure_reason or str(exc),
            )
        after_map = self._read_repo_text_map(workspace)
        changed_files = sorted([f for f in set(before_map) | set(after_map) if before_map.get(f, "") != after_map.get(f, "")])
        if not changed_files:
            cleanup_candidate_workspace(handle)
            return CandidateExecutionResult(
                hard_fail=True,
                blocked_reason="blocked_model_failure",
                attempt_summary="no_meaningful_diff",
                attempt_category="rejected_noop",
                verification_stage="stage_0",
                workspace_prep_seconds=handle.prep_seconds,
                prompt_build_seconds=prompt_build_seconds,
                model_execution_seconds=model_execution_seconds,
                candidate_total_seconds=time.monotonic() - started,
                workspace_strategy=handle.strategy,
                policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
                direct_repair_attempted=True,
                direct_repair_suspect=target_file,
                prompt_tokens_first_attempt=prompt_tokens,
                apply_mode=apply_mode,
                apply_failure_reason="no_changes_after_apply",
            )
        diff_stats = {"changed_line_count": sum(self._count_changed_lines(before_map.get(f, ""), after_map.get(f, "")) for f in changed_files)}
        artifact = self._persist_patch_artifact(attempt_id, diff_text)
        verification_started = time.monotonic()
        verification_outputs, success, score, score_breakdown = self._run_verification(workspace, changed_files, benchmark_config, WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH)
        verification_seconds = time.monotonic() - verification_started
        elapsed = time.monotonic() - started
        cleanup_candidate_workspace(handle)
        return CandidateExecutionResult(
            changed_files=changed_files,
            diff_stats=diff_stats,
            patch_artifact_path=artifact,
            diff_text=diff_text,
            verification_outputs=verification_outputs,
            score=score,
            score_breakdown=score_breakdown,
            attempt_summary=f"Evaluated direct patch touching {len(changed_files)} file(s).",
            attempt_category="candidate_verified" if success else "verification_failed",
            success=success,
            target_verification_passed=bool(verification_outputs.get("target_verification_passed", False)),
            collateral_verification_passed=bool(verification_outputs.get("collateral_verification_passed", False)),
            static_sanity_passed=bool(verification_outputs.get("static_sanity_passed", False)),
            minimality_score=float(score_breakdown.get("minimality", 0.0)),
            novelty_score=float(score_breakdown.get("novelty", 0.0)),
            verification_stage=str(verification_outputs.get("verification_stage", "stage_3")),
            target_exit_codes=list(verification_outputs.get("target_exit_codes", [])),
            target_command_count=int(verification_outputs.get("target_command_count", 0)),
            workspace_prep_seconds=handle.prep_seconds,
            prompt_build_seconds=prompt_build_seconds,
            model_execution_seconds=model_execution_seconds,
            verification_seconds=verification_seconds,
            candidate_total_seconds=elapsed,
            workspace_strategy=handle.strategy,
            policy_profile=WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value,
            direct_repair_attempted=True,
            direct_repair_suspect=target_file,
            hypothesis_stage_skipped_initially=True,
            prompt_tokens_first_attempt=prompt_tokens,
            apply_mode=apply_mode,
            apply_failure_reason=apply_failure_reason,
            meaningful_patch_produced=True,
        )

    def evaluate_guided_retry(self, *, retry_hint: str = "", **kwargs: Any) -> CandidateExecutionResult:
        kwargs["failing_test_file"] = ""
        kwargs["failing_test_contents"] = ""
        return self._evaluate_patch_attempt(**kwargs, stage_name="stage2", retry_hint=retry_hint)

    def _request_patch_output(self, prompt: str, *, stage_name: str) -> str:
        payload = {
            "model": self.runner.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            "system": [{"type": "text", "text": f"Return only a patch block for {stage_name}."}],
            "tools": [],
            "max_tokens": self.runner.max_tokens,
            "stream": False,
        }
        raw = self.runner.client.create_message(payload, stream=False)
        response = raw if isinstance(raw, dict) else {"content": []}
        content = normalize_content_blocks(response.get("content", []))
        return self._extract_final_assistant_text(content)

    def _build_direct_diff_prompt(
        self,
        *,
        objective: str,
        target_file: str,
        target_file_contents: str,
        failing_test_file: str,
        failing_test_contents: str,
        verification_target: str,
        stage_name: str = "stage1",
        retry_hint: str = "",
    ) -> str:
        parts = [
            f"Stage: {stage_name}",
            f"Objective: {objective}",
            f"Exact target file: {target_file}",
            "Edit only this exact file. Do not edit any other file.",
            "No repo exploration, planning, hypotheses, or multi-file changes.",
            f"Verification target: {verification_target or 'default configured target verification'}",
            "Verification runs immediately after patch apply.",
            "Return one format only, in this preference order:",
            "1) Unified diff for this exact file.",
            "2) Full replacement file content for this exact file.",
            "3) SNIPPET_REPLACE with exact old/new snippet.",
            "Do not write explanations before patch block.",
            "Unified diff example:",
            f"--- a/{target_file}\n+++ b/{target_file}\n@@ -1 +1 @@\n-old\n+new",
            "Full replacement example:",
            f"NEW FILE CONTENT {target_file}\n<entire file contents>",
            "Snippet replacement example:",
            "SNIPPET_REPLACE\nFILE: <target>\nOLD_SNIPPET:\n<exact old>\nNEW_SNIPPET:\n<new>",
            f"--- FILE: {target_file} ---",
            target_file_contents,
        ]
        if retry_hint:
            parts.append(f"Retry hint: {retry_hint}")
        if failing_test_file and failing_test_contents:
            parts.extend([
                f"--- SUPPORTING FAILING TEST: {failing_test_file} ---",
                failing_test_contents,
            ])
        return "\n".join(parts)

    def _apply_patch_pipeline(self, *, workspace: Path, target_file: str, target_file_contents: str, patch_text: str) -> tuple[str, str]:
        text = patch_text.strip()
        if not text:
            raise PatchApplyError("empty_patch")

        unified_error = ""
        if "--- " in text and "+++ " in text:
            try:
                parsed = parse_unified_diff(text)
                if len(parsed) != 1:
                    raise PatchApplyError("stage1_unified_diff_must_be_single_file")
                normalized_targets = {p.new_path.removeprefix("b/").removeprefix("a/") for p in parsed}
                if normalized_targets != {target_file}:
                    raise PatchApplyError("stage1_unified_diff_target_mismatch")
                apply_unified_diff(workspace, text)
                return "unified_diff", ""
            except Exception as exc:  # noqa: BLE001
                unified_error = str(exc)

        if "NEW FILE CONTENT" in text:
            idx = text.find("NEW FILE CONTENT")
            block = text[idx:]
            header, _, body = block.partition("\n")
            maybe_target = header.replace("NEW FILE CONTENT", "").strip()
            if maybe_target and maybe_target != target_file:
                raise PatchApplyError("whole_file_target_mismatch")
            (workspace / target_file).write_text(body, encoding="utf-8")
            return "whole_file", unified_error

        if "SNIPPET_REPLACE" in text and "OLD_SNIPPET:" in text and "NEW_SNIPPET:" in text:
            old_block = text.split("OLD_SNIPPET:", 1)[1].split("NEW_SNIPPET:", 1)[0].strip("\n")
            new_block = text.split("NEW_SNIPPET:", 1)[1].strip("\n")
            current = (workspace / target_file).read_text(encoding="utf-8")
            if old_block not in current:
                raise PatchApplyError("snippet_old_block_not_found")
            (workspace / target_file).write_text(current.replace(old_block, new_block, 1), encoding="utf-8")
            return "snippet_replace", unified_error

        if "--- " not in text and "+++ " not in text and "@@" not in text:
            (workspace / target_file).write_text(text, encoding="utf-8")
            return "whole_file", unified_error
        raise PatchApplyError(unified_error or "unrecognized_patch_format")

    def evaluate_candidate(
        self,
        *,
        repo_path: Path,
        objective: str,
        suspect_region: str,
        hypothesis_id: str,
        hypothesis: str,
        constraints: dict[str, Any],
        runtime_profile: str,
        benchmark_config: Any,
        baseline_handle: str,
        edit_budget: EditBudget,
        branch_failure_history: list[str],
        timeout_budget_seconds: float,
        attempt_id: str,
        max_candidate_turns: int = 8,
        max_candidate_tool_calls: int = 24,
        policy_profile: str = WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH,
        execution_mode: str = "heavy",
        session_context: WeakSearchSessionContext | None = None,
        hypothesis_stage_skipped_initially: bool = False,
    ) -> CandidateExecutionResult:
        started = time.monotonic()
        profile = WeakSearchPolicyProfile(str(policy_profile))
        direct_mode = execution_mode == "direct_repair" or profile == WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH
        handle = prepare_candidate_workspace(repo_path, fast_path=is_direct_repair_profile(profile) or direct_mode)
        workspace = handle.workspace
        try:
            before_map = self._read_repo_text_map(workspace)
            prompt_started = time.monotonic()
            prompt = self._build_prompt(
                suspect=suspect_region,
                hypothesis_text=hypothesis,
                constraints=constraints,
                failed_attempt_summary=branch_failure_history,
                runtime_profile=runtime_profile,
                baseline_handle=baseline_handle,
                policy_profile=profile.value,
                execution_mode=execution_mode,
            )
            prompt_build_seconds = time.monotonic() - prompt_started
            prompt_tokens = len(prompt.split())
            model_started = time.monotonic()
            try:
                model_err = self._run_model_edit_pass(
                    workspace,
                    prompt,
                    max_candidate_turns=max_candidate_turns,
                    max_candidate_tool_calls=max_candidate_tool_calls,
                    timeout_budget_seconds=timeout_budget_seconds,
                    execution_mode=execution_mode,
                    session_context=session_context,
                    suspect_file=suspect_region,
                )
            except TypeError:
                model_err = self._run_model_edit_pass(workspace, prompt)
            model_execution_seconds = time.monotonic() - model_started
            if model_err and not model_err.startswith("final_completion:"):
                return CandidateExecutionResult(
                    hard_fail=True,
                    blocked_reason="blocked_model_failure",
                    attempt_category="blocked_model_failure",
                    failure_signature=self._fingerprint(model_err),
                    attempt_summary=model_err,
                    prompt_summary=prompt[:220],
                    workspace_prep_seconds=handle.prep_seconds,
                    prompt_build_seconds=prompt_build_seconds,
                    model_execution_seconds=model_execution_seconds,
                    tool_execution_seconds=self._last_tool_execution_seconds,
                    candidate_total_seconds=time.monotonic() - started,
                    workspace_strategy=handle.strategy,
                    policy_profile=profile.value,
                    direct_repair_attempted=direct_mode,
                    direct_repair_suspect=suspect_region if direct_mode else "",
                    session_context_reused=bool(session_context and session_context.planning_initialized),
                    hypothesis_stage_skipped_initially=hypothesis_stage_skipped_initially,
                    prompt_tokens_first_attempt=prompt_tokens,
                    tool_calls_first_attempt=self._last_tool_calls,
                    exploration_block_triggered=self._last_exploration_block_triggered,
                )

            after_map = self._read_repo_text_map(workspace)
            changed_files = sorted([p for p in set(before_map) | set(after_map) if before_map.get(p) != after_map.get(p)])
            changed_lines = sum(self._count_changed_lines(before_map.get(p, ""), after_map.get(p, "")) for p in changed_files)
            diff_stats = {"changed_file_count": len(changed_files), "changed_line_count": changed_lines}
            diff_text = self._build_unified_diff(before_map, after_map, changed_files)

            if not changed_files or not diff_text.strip():
                return CandidateExecutionResult(
                    hard_fail=True,
                    blocked_reason="rejected_noop",
                    attempt_category="rejected_noop",
                    failure_signature="no-op",
                    attempt_summary="Model produced no meaningful repository changes.",
                    prompt_summary=prompt[:220],
                    workspace_prep_seconds=handle.prep_seconds,
                    prompt_build_seconds=prompt_build_seconds,
                    model_execution_seconds=model_execution_seconds,
                    tool_execution_seconds=self._last_tool_execution_seconds,
                    candidate_total_seconds=time.monotonic() - started,
                    workspace_strategy=handle.strategy,
                    policy_profile=profile.value,
                    direct_repair_attempted=direct_mode,
                    direct_repair_suspect=suspect_region if direct_mode else "",
                    session_context_reused=bool(session_context and session_context.planning_initialized),
                    hypothesis_stage_skipped_initially=hypothesis_stage_skipped_initially,
                    prompt_tokens_first_attempt=prompt_tokens,
                    tool_calls_first_attempt=self._last_tool_calls,
                    exploration_block_triggered=self._last_exploration_block_triggered,
                )

            policy = enforce_path_policy(
                changed_files,
                benchmark_config.allowlist_paths,
                benchmark_config.forbidden_paths,
                expected_paths=benchmark_config.expected_files,
                allowed_support_files=benchmark_config.allowed_support_files,
                allowed_support_globs=benchmark_config.allowed_support_globs,
            )
            guard = guard_candidate_diff(
                files_touched=changed_files,
                changed_lines=changed_lines,
                hunks=max(1, len(changed_files)),
                budget=edit_budget,
                benchmark_config=benchmark_config,
                formatting_only=False,
            )
            artifact = self._persist_patch_artifact(attempt_id, diff_text)
            if not guard.allowed or policy.violating_paths:
                reason = "rejected_diff_guard" if not guard.allowed else "blocked_policy"
                sig = self._fingerprint(reason + "|" + ",".join(policy.violating_paths))
                return CandidateExecutionResult(
                    changed_files=changed_files,
                    diff_stats=diff_stats,
                    patch_artifact_path=artifact,
                    diff_text=diff_text,
                    hard_fail=True,
                    blocked_reason=reason,
                    attempt_category=reason,
                    failure_signature=sig,
                    attempt_summary=guard.reason if not guard.allowed else f"Violating paths: {policy.violating_paths}",
                    prompt_summary=prompt[:220],
                    verification_stage="stage_0",
                    workspace_prep_seconds=handle.prep_seconds,
                    prompt_build_seconds=prompt_build_seconds,
                    model_execution_seconds=model_execution_seconds,
                    tool_execution_seconds=self._last_tool_execution_seconds,
                    candidate_total_seconds=time.monotonic() - started,
                    workspace_strategy=handle.strategy,
                    policy_profile=profile.value,
                    direct_repair_attempted=direct_mode,
                    direct_repair_suspect=suspect_region if direct_mode else "",
                    session_context_reused=bool(session_context and session_context.planning_initialized),
                    hypothesis_stage_skipped_initially=hypothesis_stage_skipped_initially,
                    prompt_tokens_first_attempt=prompt_tokens,
                    tool_calls_first_attempt=self._last_tool_calls,
                    exploration_block_triggered=self._last_exploration_block_triggered,
                )

            verification_started = time.monotonic()
            verification_outputs, success, score, score_breakdown = self._run_verification(workspace, changed_files, benchmark_config, profile)
            verification_seconds = time.monotonic() - verification_started
            elapsed = time.monotonic() - started
            if elapsed > timeout_budget_seconds:
                return CandidateExecutionResult(
                    changed_files=changed_files,
                    diff_stats=diff_stats,
                    patch_artifact_path=artifact,
                    diff_text=diff_text,
                    verification_outputs=verification_outputs,
                    hard_fail=True,
                    blocked_reason="blocked_timeout",
                    attempt_category="blocked_timeout",
                    failure_signature=self._fingerprint("timeout" + attempt_id),
                    attempt_summary="Candidate exceeded timeout budget.",
                    prompt_summary=prompt[:220],
                    verification_stage="timeout",
                    workspace_prep_seconds=handle.prep_seconds,
                    prompt_build_seconds=prompt_build_seconds,
                    model_execution_seconds=model_execution_seconds,
                    tool_execution_seconds=self._last_tool_execution_seconds,
                    verification_seconds=verification_seconds,
                    candidate_total_seconds=elapsed,
                    workspace_strategy=handle.strategy,
                    policy_profile=profile.value,
                    direct_repair_attempted=direct_mode,
                    direct_repair_suspect=suspect_region if direct_mode else "",
                    session_context_reused=bool(session_context and session_context.planning_initialized),
                    hypothesis_stage_skipped_initially=hypothesis_stage_skipped_initially,
                    prompt_tokens_first_attempt=prompt_tokens,
                    tool_calls_first_attempt=self._last_tool_calls,
                    exploration_block_triggered=self._last_exploration_block_triggered,
                )

            failure_sig_payload = {
                "files": changed_files,
                "verification": verification_outputs.get("summary", ""),
                "repro_fingerprint": verification_outputs.get("repro_fingerprint", ""),
                "category": self._major_error_category(verification_outputs),
            }
            attempt_category = "candidate_verified" if success else "verification_failed"
            return CandidateExecutionResult(
                changed_files=changed_files,
                diff_stats=diff_stats,
                patch_artifact_path=artifact,
                diff_text=diff_text,
                verification_outputs=verification_outputs,
                hard_fail=False,
                score=score,
                score_breakdown=score_breakdown,
                failure_signature=self._fingerprint(json.dumps(failure_sig_payload, sort_keys=True)),
                blocked_reason="",
                attempt_summary=f"Evaluated patch touching {len(changed_files)} file(s).",
                attempt_category=attempt_category,
                success=success,
                prompt_summary=prompt[:220],
                target_verification_passed=bool(verification_outputs.get("target_verification_passed", False)),
                collateral_verification_passed=bool(verification_outputs.get("collateral_verification_passed", False)),
                static_sanity_passed=bool(verification_outputs.get("static_sanity_passed", False)),
                minimality_score=float(score_breakdown.get("minimality", 0.0)),
                novelty_score=float(score_breakdown.get("novelty", 0.0)),
                verification_stage=str(verification_outputs.get("verification_stage", "stage_3")),
                target_exit_codes=list(verification_outputs.get("target_exit_codes", [])),
                target_command_count=int(verification_outputs.get("target_command_count", 0)),
                workspace_prep_seconds=handle.prep_seconds,
                prompt_build_seconds=prompt_build_seconds,
                model_execution_seconds=model_execution_seconds,
                tool_execution_seconds=self._last_tool_execution_seconds,
                verification_seconds=verification_seconds,
                candidate_total_seconds=elapsed,
                workspace_strategy=handle.strategy,
                policy_profile=profile.value,
                direct_repair_attempted=direct_mode,
                direct_repair_suspect=suspect_region if direct_mode else "",
                session_context_reused=bool(session_context and session_context.planning_initialized),
                hypothesis_stage_skipped_initially=hypothesis_stage_skipped_initially,
                prompt_tokens_first_attempt=prompt_tokens,
                tool_calls_first_attempt=self._last_tool_calls,
                exploration_block_triggered=self._last_exploration_block_triggered,
            )
        finally:
            cleanup_candidate_workspace(handle)

    def commit_candidate(self, repo_path: Path, candidate_result: CandidateExecutionResult) -> None:
        if candidate_result.hard_fail or not candidate_result.diff_text.strip():
            raise ValueError("Only successful evaluated candidates with a patch may be committed")
        apply_unified_diff(repo_path, candidate_result.diff_text)

    def evaluate(self, **kwargs: Any) -> CandidateExecutionResult:
        return self.evaluate_candidate(**kwargs)

    def _build_prompt(self, *, suspect: str, hypothesis_text: str, constraints: dict[str, Any], failed_attempt_summary: list[str], runtime_profile: str, baseline_handle: str, policy_profile: str, execution_mode: str) -> str:
        if execution_mode == "direct_repair" or policy_profile == WeakSearchPolicyProfile.DIRECT_REPAIR_FAST_PATH.value:
            return self._build_direct_repair_prompt(suspect=suspect, hypothesis_text=hypothesis_text, constraints=constraints, failed_attempt_summary=failed_attempt_summary)
        return (
            f"Objective: {self.instruction}\n"
            f"Runtime profile: {runtime_profile}\n"
            f"Baseline: {baseline_handle}\n"
            f"Suspect region: {suspect}\n"
            f"Hypothesis: {hypothesis_text}\n"
            f"Constraints: {json.dumps(constraints)}\n"
            f"Failed attempts: {failed_attempt_summary[-3:]}\n"
            "Apply minimal concrete edits in repository files and stop when done."
        )

    def _build_direct_repair_prompt(self, *, suspect: str, hypothesis_text: str, constraints: dict[str, Any], failed_attempt_summary: list[str]) -> str:
        has_verification = bool(constraints.get("visible_verification") or constraints.get("expected_files"))
        lines = [
            f"Objective: {self.instruction}",
            f"Exact implementation target file: {suspect}",
            f"Failure evidence: {hypothesis_text}",
            f"Verification target: {'provided' if has_verification else 'default'}",
            "bounded local repair.",
            "Inspect the target implementation file first.",
            "Edit only target unless impossible.",
            "Broad exploration is not allowed.",
            "Read one failing test only if needed.",
            "Make the smallest valid patch, verify, and stop.",
        ]
        return "\n".join(lines)

    def _run_model_edit_pass(self, workspace: Path, prompt: str, *, max_candidate_turns: int, max_candidate_tool_calls: int, timeout_budget_seconds: float, execution_mode: str, session_context: WeakSearchSessionContext | None, suspect_file: str) -> str:
        original_repo = self.runner.repo
        start = time.monotonic()
        turns = 0
        tool_calls = 0
        tool_seconds = 0.0
        try:
            self.runner.repo = workspace
            if session_context is not None and (not session_context.planning_initialized or session_context.plan_invalidated):
                self.runner._ensure_project_memory_and_plan(session_context.planning_prompt)
                session_context.planning_initialized = True
                session_context.plan_invalidated = False
            messages = build_initial_messages(workspace, prompt)
            system = build_system_blocks(workspace, benchmark_config=self.runner.benchmark_config)
            continuation_used = False
            touched_suspect = False
            inspected_suspect = False
            secondary_reads = 0
            self._last_assistant_completion_text = ""
            self._last_tool_calls = 0
            self._last_exploration_block_triggered = False
            while turns < max_candidate_turns and tool_calls < max_candidate_tool_calls:
                if time.monotonic() - start > timeout_budget_seconds:
                    return "candidate execution timeout"
                payload = {
                    "model": self.runner.model,
                    "messages": messages,
                    "system": system,
                    "tools": tool_specs(),
                    "max_tokens": self.runner.max_tokens,
                    "stream": False,
                }
                raw = self.runner.client.create_message(payload, stream=False)
                response = raw if isinstance(raw, dict) else {"content": []}
                content = normalize_content_blocks(response.get("content", []))
                messages.append({"role": "assistant", "content": content})
                turns += 1
                tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
                if not tool_uses:
                    final_text = self._extract_final_assistant_text(content)
                    self._last_assistant_completion_text = final_text
                    return f"final_completion:{final_text}" if final_text else "empty assistant completion"
                tool_results: list[dict[str, Any]] = []
                for block in tool_uses:
                    if tool_calls >= max_candidate_tool_calls:
                        break
                    tool_calls += 1
                    self._last_tool_calls = tool_calls
                    tool_name = str(block.get("name", ""))
                    tool_input = dict(block.get("input", {}))
                    tool_use_id = str(block.get("id", f"cand-tool-{tool_calls}"))
                    tool_start = time.monotonic()
                    if execution_mode == "direct_repair":
                        path_hint = str(tool_input.get("file_path") or tool_input.get("path") or tool_input.get("dir") or "")
                        broad_ls = tool_name == "Ls" and path_hint.strip() in {"", ".", "src", "src/", "tests", "tests/"}
                        broad_probe = tool_name in {"Glob", "Search", "Grep", "GitLog", "GitBranch", "GitCheckout"} or broad_ls
                        inspects_target = tool_name in {"Read", "View", "Cat"} and bool(suspect_file) and suspect_file in path_hint
                        is_read = tool_name in {"Read", "View", "Cat"}
                        unrelated_config_read = is_read and path_hint in {"pyproject.toml", "setup.cfg", "tox.ini", "package.json"}
                        if tool_calls == 1 and not inspects_target:
                            self._last_exploration_block_triggered = True
                            return f"direct_repair_thrash:first_tool_not_target_inspection:{tool_name}"
                        if broad_probe and not inspected_suspect:
                            self._last_exploration_block_triggered = True
                            return f"direct_repair_thrash:{tool_name}"
                        if unrelated_config_read and not touched_suspect:
                            self._last_exploration_block_triggered = True
                            return "direct_repair_thrash:unrelated_config_before_patch"
                        if is_read and inspected_suspect and (not inspects_target) and (not path_hint.startswith("tests/")) and not touched_suspect:
                            self._last_exploration_block_triggered = True
                            return "direct_repair_thrash:unrelated_read_before_patch"
                        if is_read and path_hint.startswith("tests/") and not touched_suspect:
                            secondary_reads += 1
                            if secondary_reads > 1:
                                self._last_exploration_block_triggered = True
                                return "direct_repair_thrash:too_many_secondary_reads"
                        if inspects_target:
                            inspected_suspect = True
                    result = self.runner._execute_tool_with_policy(tool_name, tool_input, tool_use_id, len(messages))
                    tool_seconds += time.monotonic() - tool_start
                    if execution_mode == "direct_repair" and tool_name in {"Write", "Patch"}:
                        target = str(tool_input.get("file_path") or tool_input.get("path") or "")
                        if suspect_file and suspect_file in target:
                            touched_suspect = True
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(result.get("content", "")),
                            "is_error": bool(result.get("is_error", False)),
                        }
                    )
                if not tool_results:
                    return "candidate interaction budget reached before tool execution"
                messages.append({"role": "user", "content": tool_results})
                if execution_mode == "direct_repair":
                    if continuation_used:
                        return "direct_repair_exceeded_continuation_policy"
                    if not touched_suspect:
                        if not inspected_suspect:
                            return "direct_repair_no_progress_on_suspect_file"
                        continuation_used = True
                        max_candidate_turns = min(max_candidate_turns, 2)
                        continue
                    continuation_used = True
                    max_candidate_turns = min(max_candidate_turns, 2)
            return "candidate interaction budget exceeded"
        except Exception as exc:  # noqa: BLE001
            return str(exc)
        finally:
            self._last_tool_execution_seconds = tool_seconds
            self._last_tool_calls = tool_calls
            self.runner.repo = original_repo

    @staticmethod
    def _extract_final_assistant_text(content: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", ""))
            if block_type == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    parts.append(text)
                continue
            if block_type == "output_text":
                text = str(block.get("content", "")).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    def _run_verification(self, workspace: Path, changed_files: list[str], benchmark_config: Any, profile: WeakSearchPolicyProfile) -> tuple[dict[str, Any], bool, float, dict[str, float]]:
        outputs: dict[str, Any] = {
            "commands": [],
            "changed_files": changed_files,
            "verification_stage": "stage_0",
        }
        stage0_patch_sanity = bool(changed_files)
        stage0_syntax_ok = self._syntax_sanity(workspace, changed_files)
        outputs["verification_stage"] = "stage_1"
        stage1_imports_ok = self._import_sanity(workspace, changed_files)

        commands = list(benchmark_config.visible_verification) or ["python -m pytest -q"]
        target_passed = True
        target_exit_codes: list[int] = []
        for cmd in commands:
            proc = subprocess.run(cmd, shell=True, cwd=workspace, text=True, capture_output=True)
            target_exit_codes.append(proc.returncode)
            outputs["commands"].append({"command": cmd, "exit_code": proc.returncode, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]})
            if proc.returncode != 0:
                target_passed = False
                if is_direct_repair_profile(profile):
                    break

        outputs["verification_stage"] = "stage_3"
        outputs["target_verification_passed"] = target_passed
        outputs["collateral_verification_passed"] = target_passed
        outputs["static_sanity_passed"] = stage0_syntax_ok and stage1_imports_ok
        outputs["summary"] = "ok" if target_passed and outputs["static_sanity_passed"] else "verification_failed"
        outputs["target_exit_codes"] = target_exit_codes
        outputs["target_command_count"] = len(target_exit_codes)

        if benchmark_config.task_id and "repro" in benchmark_config.task_id:
            outputs["repro_command"] = commands[0]
            fingerprint_basis = "\n".join(f"{c['exit_code']}|{c['stderr']}|{c['stdout']}" for c in outputs["commands"])
            outputs["repro_fingerprint"] = self._fingerprint(fingerprint_basis)

        unexpected_file_penalty = 0.0
        if getattr(benchmark_config, "expected_files", None):
            unexpected = [f for f in changed_files if f not in benchmark_config.expected_files]
            unexpected_file_penalty = min(1.0, len(unexpected) / max(1, len(changed_files)))

        minimality = max(0.0, 1.0 - (len(changed_files) / max(1, self.edit_budget.max_files)))
        novelty = 1.0 if len(changed_files) <= 1 else 0.5
        score_inputs = {
            "patch_applies": stage0_patch_sanity,
            "syntax_ok": stage0_syntax_ok,
            "imports_ok": stage1_imports_ok,
            "forbidden_path": False,
            "target_verification": 1.0 if target_passed else 0.0,
            "collateral_verification": 1.0 if target_passed else 0.0,
            "static_sanity": 1.0 if outputs["static_sanity_passed"] else 0.0,
            "constraint_consistency": max(0.0, 1.0 - unexpected_file_penalty),
            "minimality": minimality,
            "novelty": novelty,
        }
        verification = run_staged_verifier(score_inputs)
        tool_penalty = min(0.15, self._last_tool_execution_seconds / 60.0)
        score = max(0.0, verification.score - tool_penalty)
        outputs["gate_reason"] = verification.gate.reason
        score_breakdown = {
            "target_verification": score_inputs["target_verification"],
            "collateral_verification": score_inputs["collateral_verification"],
            "static_sanity": score_inputs["static_sanity"],
            "constraint_consistency": score_inputs["constraint_consistency"],
            "minimality": minimality,
            "novelty": novelty,
            "tool_efficiency_penalty": tool_penalty,
        }
        success = not verification.gate.hard_fail and target_passed and outputs["static_sanity_passed"]
        return outputs, success, score, score_breakdown

    def _major_error_category(self, verification_outputs: dict[str, Any]) -> str:
        if verification_outputs.get("summary") == "ok":
            return "none"
        if verification_outputs.get("repro_fingerprint"):
            return "repro_failure"
        return "verification_failure"

    def _persist_patch_artifact(self, attempt_id: str, diff_text: str) -> str:
        out_dir = self.runner.repo / ".villani_code" / "patches"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{attempt_id}.diff"
        out.write_text(diff_text, encoding="utf-8")
        return str(out.relative_to(self.runner.repo))

    def _build_unified_diff(self, before: dict[str, str], after: dict[str, str], changed_files: list[str]) -> str:
        chunks: list[str] = []
        for rel in changed_files:
            b = before.get(rel, "").splitlines(keepends=True)
            a = after.get(rel, "").splitlines(keepends=True)
            diff = difflib.unified_diff(b, a, fromfile=f"a/{rel}", tofile=f"b/{rel}")
            chunks.extend(list(diff))
        return "".join(chunks)

    def _read_repo_text_map(self, root: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith(".git/") or rel.startswith(".villani_code/"):
                continue
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
        return out

    def _count_changed_lines(self, before: str, after: str) -> int:
        return sum(1 for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line.startswith("+") or line.startswith("-"))

    def _syntax_sanity(self, workspace: Path, changed_files: list[str]) -> bool:
        py_files = [f for f in changed_files if f.endswith(".py")]
        if not py_files:
            return True
        proc = subprocess.run(["python", "-m", "py_compile", *py_files], cwd=workspace, capture_output=True, text=True)
        return proc.returncode == 0

    def _import_sanity(self, workspace: Path, changed_files: list[str]) -> bool:
        py_files = [f for f in changed_files if f.endswith(".py")]
        if not py_files:
            return True
        snippet = "import pathlib, sys\n" "[compile(pathlib.Path(p).read_text(), p, 'exec') for p in sys.argv[1:]]\n"
        proc = subprocess.run(["python", "-c", snippet, *py_files], cwd=workspace, capture_output=True, text=True)
        return proc.returncode == 0

    def _fingerprint(self, payload: str) -> str:
        return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:12]
