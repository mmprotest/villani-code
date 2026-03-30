from __future__ import annotations

import py_compile
import re
from pathlib import Path
from typing import Any

from villani_code.patch_apply import PatchApplyError, extract_unified_diff_targets
from villani_code.permissions import Decision
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.tools import execute_tool


_FENCED_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)


def _extract_fenced_code(text: str) -> str | None:
    match = _FENCED_BLOCK_RE.search(text)
    if not match:
        return None
    return match.group(1).strip("\n")


def _benchmark_mutation_targets(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    if tool_name == "Write":
        path = str(tool_input.get("file_path", ""))
        return [path] if path else []
    if tool_name == "Patch":
        diff = str(tool_input.get("unified_diff", ""))
        default_path = str(tool_input.get("file_path", "") or "") or None
        try:
            return extract_unified_diff_targets(diff, default_file_path=default_path)
        except PatchApplyError:
            return [default_path] if default_path else []
    return []


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


def _benchmark_post_write_python_validation(
    runner: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    if result.get("is_error"):
        return result
    if not runner.benchmark_config.enabled or tool_name not in {"Write", "Patch"}:
        return result

    targets = _benchmark_mutation_targets(tool_name, tool_input)
    py_targets = []
    for target in targets:
        normalized = str(target or "").replace("\\", "/").lstrip("./")
        if not normalized.endswith(".py"):
            continue
        root = runner.active_tool_root if hasattr(runner, "active_tool_root") else runner.repo
        candidate = Path(normalized)
        abs_target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if abs_target.exists() and abs_target.is_file():
            py_targets.append((normalized, abs_target))

    if not py_targets:
        return result

    for rel, abs_path in py_targets:
        try:
            py_compile.compile(str(abs_path), doraise=True)
        except py_compile.PyCompileError as exc:
            message = str(getattr(exc, "msg", "") or str(exc)).strip()
            event_payload = {
                "type": "benchmark_post_write_validation_failed",
                "file_path": rel,
                "validator": "py_compile",
                "exception_type": exc.__class__.__name__,
                "message": message,
            }
            runner.event_callback(event_payload)
            runner.event_callback(
                {
                    "type": "failure_classified",
                    "category": "benchmark_post_write_validation_failed",
                    "summary": message,
                    "next_strategy": f"Repair Python syntax in {rel} and retry a minimal patch.",
                    "occurrence": 1,
                    "failed_files": [rel],
                }
            )
            return {
                "is_error": True,
                "content": (
                    "Benchmark post-write validation failed. "
                    f"file={rel} validator=py_compile error_type={exc.__class__.__name__} error={message}. "
                    "Repair only this file with a minimal follow-up patch."
                ),
            }
    return result


def _validate_benchmark_mutation(runner: Any, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    config = runner.benchmark_config
    if not config.enabled or tool_name not in {"Write", "Patch"}:
        return None
    targets = _benchmark_mutation_targets(tool_name, tool_input)
    if not targets:
        return f"benchmark_policy_denied: task_id={config.task_id} reason=no_target_paths"
    normalized_targets = [config.normalized_path(path) for path in targets]
    if len(set(normalized_targets)) > config.max_files_touched:
        return (
            f"benchmark_policy_denied: task_id={config.task_id} reason=max_files_touched_exceeded "
            f"limit={config.max_files_touched} touched={len(set(normalized_targets))}"
        )
    for raw_path, path in zip(targets, normalized_targets):
        if not config.in_allowlist(path):
            return f"benchmark_policy_denied: task_id={config.task_id} reason=outside_allowlist path={path}"
        if config.in_forbidden(path):
            return f"benchmark_policy_denied: task_id={config.task_id} reason=forbidden_path path={path}"
        if not config.is_expected_or_support(path):
            return f"benchmark_policy_denied: task_id={config.task_id} reason=not_expected_or_support path={path}"
        classification = classify_repo_path(path)
        if is_ignored_repo_path(path) or classification in {"runtime_artifact", "editor_artifact", "vcs_internal"}:
            return f"benchmark_policy_denied: task_id={config.task_id} reason=ignored_or_runtime_artifact path={path}"
    return None


def _parse_benchmark_denial_message(message: str) -> tuple[str, str | None]:
    reason = "policy_denied"
    path: str | None = None
    for part in message.split():
        if part.startswith("reason="):
            reason = part.split("=", 1)[1]
        if part.startswith("path="):
            path = part.split("=", 1)[1]
    return reason, path


def _benchmark_denial_feedback(runner: Any, denial_message: str, paths: list[str]) -> str:
    reason, parsed_path = _parse_benchmark_denial_message(denial_message)
    denied_path = (parsed_path or (paths[0] if paths else "")).strip() or "unknown"
    expected = [str(p) for p in runner.benchmark_config.expected_files[:3] if str(p).strip()]
    support = [str(p) for p in runner.benchmark_config.allowed_support_files[:3] if str(p).strip()]
    allowed_targets = expected + [p for p in support if p not in expected]
    allowed_preview = ", ".join(allowed_targets[:4]) if allowed_targets else "none listed"
    return (
        "Benchmark policy blocked this mutation. "
        f"Denied path: {denied_path}. "
        f"Reason: {reason}. "
        f"Allowed expected/support targets: {allowed_preview}. "
        "Retry with a single in-scope patch to one allowed target file."
    )


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
    targets = _benchmark_mutation_targets(tool_name, tool_input)
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


def _greenfield_mutation_targets(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    if tool_name == "Write":
        target = str(tool_input.get("file_path", "")).strip()
        return [target] if target else []
    if tool_name == "Patch":
        diff = str(tool_input.get("unified_diff", ""))
        default_path = str(tool_input.get("file_path", "") or "") or None
        try:
            return extract_unified_diff_targets(diff, default_file_path=default_path)
        except PatchApplyError:
            return [default_path] if default_path else []
    return []


def _greenfield_write_size_bytes(tool_name: str, tool_input: dict[str, Any]) -> int:
    if tool_name == "Write":
        return len(str(tool_input.get("content", "")).encode("utf-8"))
    if tool_name == "Patch":
        return len(str(tool_input.get("unified_diff", "")).encode("utf-8"))
    return 0


def _validate_greenfield_mutation_budget(
    runner: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    phase_policy: dict[str, Any],
) -> str | None:
    if tool_name not in {"Write", "Patch"}:
        return None
    if str(phase_policy.get("mission_type", "")) != "greenfield_build":
        return None
    if not bool(phase_policy.get("allow_mutating_tools", False)):
        return "greenfield_write_blocked: write not allowed in this phase"

    node_id = str(phase_policy.get("node_id", "")).strip() or str(phase_policy.get("node_phase", "")).strip()
    state = dict(getattr(runner, "_greenfield_budget_state", {}) or {})
    if str(state.get("node_id", "")) != node_id:
        state = {"node_id": node_id, "distinct_paths": set(), "new_files": set(), "total_write_bytes": 0}

    distinct_paths = set(state.get("distinct_paths", set()) or set())
    new_files = set(state.get("new_files", set()) or set())
    total_write_bytes = int(state.get("total_write_bytes", 0) or 0)
    active_root = runner.active_tool_root if hasattr(runner, "active_tool_root") else runner.repo
    targets = _greenfield_mutation_targets(tool_name, tool_input)
    normalized_targets: list[str] = []
    projected_new = 0

    for raw_target in targets:
        if not str(raw_target).strip():
            continue
        candidate = Path(str(raw_target)).expanduser()
        target_path = candidate.resolve() if candidate.is_absolute() else (active_root / candidate).resolve()
        try:
            rel = str(target_path.relative_to(active_root)).replace("\\", "/").lstrip("./")
        except ValueError:
            return f"greenfield_write_blocked: exceeded workspace boundary (path={target_path})"
        normalized_targets.append(rel)
        if tool_name == "Write" and rel not in new_files and not target_path.exists():
            projected_new += 1

    projected_distinct_paths = len(distinct_paths | set(normalized_targets))
    max_distinct_paths = int(phase_policy.get("max_distinct_paths_per_node", 0) or 0)
    if max_distinct_paths > 0 and projected_distinct_paths > max_distinct_paths:
        return (
            f"greenfield_write_blocked: exceeded path count budget "
            f"(limit={max_distinct_paths}, attempted={projected_distinct_paths})"
        )

    max_new_files = int(phase_policy.get("max_new_files_per_node", 0) or 0)
    projected_new_files = len(new_files) + projected_new
    if max_new_files > 0 and projected_new_files > max_new_files:
        return (
            f"greenfield_write_blocked: exceeded new-file budget "
            f"(limit={max_new_files}, attempted={projected_new_files})"
        )

    max_total_write_bytes = int(phase_policy.get("max_total_write_bytes_per_node", 0) or 0)
    projected_write_bytes = total_write_bytes + _greenfield_write_size_bytes(tool_name, tool_input)
    if max_total_write_bytes > 0 and projected_write_bytes > max_total_write_bytes:
        return (
            f"greenfield_write_blocked: exceeded byte budget "
            f"(limit={max_total_write_bytes}, attempted={projected_write_bytes})"
        )
    return None


def _record_greenfield_mutation_budget(
    runner: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    phase_policy: dict[str, Any],
    result: dict[str, Any],
) -> None:
    if result.get("is_error"):
        return
    if tool_name not in {"Write", "Patch"}:
        return
    if str(phase_policy.get("mission_type", "")) != "greenfield_build":
        return
    node_id = str(phase_policy.get("node_id", "")).strip() or str(phase_policy.get("node_phase", "")).strip()
    state = dict(getattr(runner, "_greenfield_budget_state", {}) or {})
    if str(state.get("node_id", "")) != node_id:
        state = {"node_id": node_id, "distinct_paths": set(), "new_files": set(), "total_write_bytes": 0}

    distinct_paths = set(state.get("distinct_paths", set()) or set())
    new_files = set(state.get("new_files", set()) or set())
    total_write_bytes = int(state.get("total_write_bytes", 0) or 0)
    active_root = runner.active_tool_root if hasattr(runner, "active_tool_root") else runner.repo

    for raw_target in _greenfield_mutation_targets(tool_name, tool_input):
        candidate = Path(str(raw_target)).expanduser()
        target_path = candidate.resolve() if candidate.is_absolute() else (active_root / candidate).resolve()
        try:
            rel = str(target_path.relative_to(active_root)).replace("\\", "/").lstrip("./")
        except ValueError:
            continue
        distinct_paths.add(rel)
        if tool_name == "Write" and target_path.exists():
            new_files.add(rel)

    state["node_id"] = node_id
    state["distinct_paths"] = distinct_paths
    state["new_files"] = new_files
    state["total_write_bytes"] = total_write_bytes + _greenfield_write_size_bytes(tool_name, tool_input)
    runner._greenfield_budget_state = state


def _validate_greenfield_shell_command(command: str, phase: str, allow_validation_shell: bool) -> str | None:
    lowered = str(command or "").strip().lower()
    if not lowered:
        return "greenfield_shell_blocked: empty command is not allowed"

    readonly_prefixes = (
        "pwd", "ls", "dir", "cat ", "type ", "rg ", "grep ", "find ", "head ", "tail ", "wc ",
    )
    validation_prefixes = (
        "pytest",
        "python -m pytest",
        "python -m compileall",
        "python3 -m compileall",
        "python ",
        "python3 ",
    )
    mutating_or_dangerous_markers = (
        "&&", "||", ";", "|", ">", "<", "`", "$(",
        " rm ", " mv ", " cp ", " chmod ", " chown ", " mkdir ", " rmdir ", " touch ",
        "curl ", "wget ", "ssh ", "scp ", "ftp ", "pip install", "npm install",
        "git add", "git commit", "git push", "git pull", "git checkout", "git switch", "git reset",
    )
    framed = f" {lowered} "
    marker = next((m for m in mutating_or_dangerous_markers if m in framed), "")
    if marker:
        return (
            f"greenfield_shell_blocked: allowlist rejected command during {phase or 'unknown_phase'} "
            f"(rule=dangerous_marker marker={marker.strip()})"
        )
    if any(lowered.startswith(prefix) for prefix in readonly_prefixes):
        return None
    if allow_validation_shell and any(lowered.startswith(prefix) for prefix in validation_prefixes):
        return None
    return (
        f"greenfield_shell_blocked: allowlist rejected command during {phase or 'unknown_phase'} "
        "(rule=not_in_safe_allowlist)"
    )


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

    phase_policy = dict(getattr(runner, "_villani_phase_tool_policy", {}) or {})
    if str(phase_policy.get("mission_type", "")) == "greenfield_build":
        phase = str(phase_policy.get("node_phase", "")).strip()
        allow_mutating_tools = bool(phase_policy.get("allow_mutating_tools", False))
        allow_shell_commands = bool(phase_policy.get("allow_shell_commands", False))
        allow_validation_shell = bool(phase_policy.get("allow_validation_shell", False))
        if tool_name in {"Write", "Patch", "Edit"} and not allow_mutating_tools:
            return {
                "content": f"Greenfield phase policy blocked mutation tool '{tool_name}' during {phase or 'unknown_phase'}",
                "is_error": True,
            }
        if tool_name == "Bash":
            command = str(tool_input.get("command", "")).strip().lower()
            if not allow_shell_commands:
                return {
                    "content": f"Greenfield phase policy blocked shell usage during {phase or 'unknown_phase'}",
                    "is_error": True,
                }
            shell_error = _validate_greenfield_shell_command(command, phase, allow_validation_shell)
            if shell_error:
                return {
                    "content": shell_error,
                    "is_error": True,
                }
        if tool_name in {"Write", "Patch"}:
            mutation_error = _validate_greenfield_mutation_budget(runner, tool_name, tool_input, phase_policy)
            if mutation_error:
                return {"content": mutation_error, "is_error": True}

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

    benchmark_violation = _validate_benchmark_mutation(runner, tool_name, tool_input)
    if benchmark_violation:
        paths = _benchmark_mutation_targets(tool_name, tool_input)
        reason_code, denied_path = _parse_benchmark_denial_message(benchmark_violation)
        correction = _benchmark_denial_feedback(runner, benchmark_violation, paths)
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
            root = runner.active_tool_root if hasattr(runner, "active_tool_root") else runner.repo
            candidate = Path(target)
            target_path = candidate.resolve() if candidate.is_absolute() else (root / normalized_target).resolve()
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
    active_root = runner.active_tool_root if hasattr(runner, "active_tool_root") else runner.repo
    result = execute_tool(
        tool_name,
        tool_input,
        active_root,
        unsafe=runner.unsafe,
        root_label=runner.active_tool_root_label if hasattr(runner, "active_tool_root_label") else "repository",
    )
    _record_greenfield_mutation_budget(runner, tool_name, tool_input, phase_policy, result)
    return _benchmark_post_write_python_validation(runner, tool_name, tool_input, result)
