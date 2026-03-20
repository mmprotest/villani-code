from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from villani_code.benchmark.tool_policy import (
    benchmark_denial_feedback,
    benchmark_mutation_targets,
    benchmark_post_write_python_validation,
    parse_benchmark_denial_message,
    validate_benchmark_mutation,
)
from villani_code.permissions import Decision
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.tools import execute_tool


_FENCED_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


def _extract_fenced_code(text: str) -> str | None:
    match = _FENCED_BLOCK_RE.search(text)
    if not match:
        return None
    return match.group(1).strip("\n")


def _normalize_mutation_payload_for_code_files(tool_name: str, tool_input: dict[str, Any]) -> None:
    if tool_name == "Write":
        file_path = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
        if not file_path.endswith(".py"):
            return
        content = str(tool_input.get("content", ""))
        extracted = _extract_fenced_code(content)
        if extracted is not None:
            tool_input["content"] = extracted + ("\n" if not extracted.endswith("\n") else "")
        return
    if tool_name == "Patch":
        diff = str(tool_input.get("unified_diff", ""))
        extracted = _extract_fenced_code(diff)
        if extracted is not None and "--- " in extracted and "+++ " in extracted:
            tool_input["unified_diff"] = extracted + ("\n" if not extracted.endswith("\n") else "")


def _validate_first_attempt_locked_target_mutation(
    runner: Any, tool_name: str, tool_input: dict[str, Any]
) -> str | None:
    if tool_name not in {"Write", "Patch"}:
        return None
    if not bool(getattr(runner, "_first_attempt_write_lock_active", False)):
        return None
    locked_target = str(getattr(runner, "_first_attempt_locked_target", "") or "").replace("\\", "/").lstrip("./")
    if not locked_target:
        return None
    targets = benchmark_mutation_targets(tool_name, tool_input)
    normalized = sorted(
        {
            str(path or "").replace("\\", "/").lstrip("./")
            for path in targets
            if str(path or "").strip()
        }
    )
    extras = [path for path in normalized if path != locked_target]
    if extras:
        runner.event_callback(
            {
                "type": "first_attempt_scope_violation",
                "failure_class": "first_attempt_scope_violation",
                "target_file": locked_target,
                "changed_files": normalized,
                "rejected_extra_files": extras,
            }
        )
        runner.event_callback(
            {
                "type": "failure_classified",
                "category": "first_attempt_scope_violation",
                "summary": f"First-attempt write lock violated; extra files: {extras}",
                "next_strategy": f"Retry with edits restricted to {locked_target}.",
                "occurrence": 1,
                "failed_files": normalized,
            }
        )
        return (
            f"first_attempt_scope_violation: target_file={locked_target} "
            f"changed_files={normalized} rejected_extra_files={extras}"
        )
    return None


def execute_tool_with_policy(
    runner: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_use_id: str,
    message_count: int,
) -> dict[str, Any]:
    hook_pre = runner.hooks.run_event(
        "PreToolUse",
        {"event": "PreToolUse", "tool": tool_name, "input": tool_input},
    )
    if not hook_pre.allow:
        return {"content": f"Blocked by hook: {hook_pre.reason}", "is_error": True}

    if tool_name == "SubmitPlan":
        if not getattr(runner, "_planning_read_only", False):
            return {"content": "SubmitPlan is available only in planning mode", "is_error": True}
        return {"content": "Plan artifact accepted", "is_error": False}

    if getattr(runner, "_planning_read_only", False):
        if tool_name in {"Write", "Patch", "Edit"}:
            return {"content": "Planning mode is read-only: file mutation tools are blocked", "is_error": True}
        if tool_name == "Bash":
            command = str(tool_input.get("command", "")).strip().lower()
            readonly_prefixes = (
                "pwd", "ls", "cat", "rg", "grep", "find", "head", "tail", "wc",
                "git status", "git diff", "git log", "git show", "git branch", "git rev-parse", "git ls-files",
                "pytest", "python -m pytest", "uv run pytest", "poetry run pytest",
            )
            mutating_markers = (
                " >", " >>", "| tee", "sed -i", " mv ", " cp ", " rm ", " chmod ", " chown ", " touch ", " mkdir ",
                "git add", "git commit", "git push", "git pull", "git merge", "git rebase", "git checkout", "git switch", "git restore", "git reset", "git clean", "git tag", "git cherry-pick",
            )
            if any(marker in f" {command} " for marker in mutating_markers):
                return {"content": "Planning mode is read-only: mutating shell command blocked", "is_error": True}
            if not any(command.startswith(prefix) for prefix in readonly_prefixes):
                return {"content": "Planning mode is read-only: shell command is not on read-only allowlist", "is_error": True}

    if runner.small_model or runner.villani_mode or runner.benchmark_config.enabled:
        policy_error = runner._small_model_tool_guard(tool_name, tool_input)
        if policy_error:
            return {"content": policy_error, "is_error": True}
        if runner.small_model:
            runner._tighten_tool_input(tool_name, tool_input)
    if runner.benchmark_config.enabled and tool_name in {"Write", "Patch"}:
        _normalize_mutation_payload_for_code_files(tool_name, tool_input)

    policy = runner.permissions.evaluate_with_reason(
        tool_name,
        tool_input,
        bypass=runner.bypass_permissions,
        auto_accept_edits=runner.auto_accept_edits,
    )
    runner._emit_policy_event(tool_name, tool_input, policy.decision, policy.reason)
    if policy.decision == Decision.DENY:
        return {"content": "Denied by permission policy", "is_error": True}
    if policy.decision == Decision.ASK:
        if runner.villani_mode:
            runner.event_callback(
                {
                    "type": "approval_auto_resolved",
                    "name": tool_name,
                    "input": tool_input,
                }
            )
        else:
            runner.event_callback(
                {
                    "type": "approval_required",
                    "name": tool_name,
                    "input": tool_input,
                }
            )
            if not runner.approval_callback(tool_name, tool_input):
                return {"content": "User denied tool execution", "is_error": True}
    elif runner.plan_mode != "off" and tool_name in {"Write", "Patch"}:
        return {"content": "Plan mode: edit not executed", "is_error": False}

    first_attempt_lock_violation = _validate_first_attempt_locked_target_mutation(runner, tool_name, tool_input)
    if first_attempt_lock_violation:
        return {"content": first_attempt_lock_violation, "is_error": True}

    benchmark_violation = validate_benchmark_mutation(runner, tool_name, tool_input)
    if benchmark_violation:
        paths = benchmark_mutation_targets(tool_name, tool_input)
        reason_code, denied_path = parse_benchmark_denial_message(benchmark_violation)
        correction = benchmark_denial_feedback(runner, benchmark_violation, paths)
        event_type = "benchmark_write_blocked" if tool_name == "Write" else "benchmark_patch_blocked"
        runner.event_callback(
            {
                "type": event_type,
                "task_id": runner.benchmark_config.task_id,
                "tool": tool_name,
                "input": tool_input,
                "reason": benchmark_violation,
                "reason_code": reason_code,
                "denied_path": denied_path,
                "paths": paths,
                "allowed_expected_files": list(runner.benchmark_config.expected_files),
                "allowed_support_files": list(runner.benchmark_config.allowed_support_files),
                "feedback": correction,
            }
        )
        return {"content": correction, "is_error": True}

    if runner.villani_mode and tool_name in {"Write", "Patch"}:
        target = str(tool_input.get("file_path", ""))
        if target:
            classification = classify_repo_path(target)
            if is_ignored_repo_path(target) or classification in {
                "runtime_artifact",
                "editor_artifact",
                "vcs_internal",
            }:
                msg = f"Skipped low-authority path: {target} ({classification})"
                runner.event_callback({"type": "autonomous_phase", "phase": msg})
                return {"content": msg, "is_error": True}

    if tool_name in {"Write", "Patch"}:
        target = str(tool_input.get("file_path", ""))
        if target:
            normalized_target = target.replace("\\", "/").lstrip("./")
            runner._intended_targets.add(normalized_target)
            runner._current_verification_targets = {normalized_target}
            runner._current_verification_before_contents = {}
            target_path = (runner.repo / normalized_target).resolve()
            if target_path.exists() and target_path.is_file():
                before_text = target_path.read_text(encoding="utf-8", errors="replace")
                runner._before_contents[normalized_target] = before_text
                runner._current_verification_before_contents[normalized_target] = before_text
        checkpoint_target = str(tool_input.get("file_path", "")).strip()
        if checkpoint_target:
            runner.checkpoints.create(
                [Path(checkpoint_target)],
                message_index=message_count,
            )
    runner.event_callback(
        {
            "type": "tool_started",
            "name": tool_name,
            "input": tool_input,
            "tool_use_id": tool_use_id,
        }
    )
    result = execute_tool(tool_name, tool_input, runner.repo, unsafe=runner.unsafe)
    return benchmark_post_write_python_validation(runner, tool_name, tool_input, result)
