from __future__ import annotations

import py_compile
from typing import Any

from villani_code.patch_apply import PatchApplyError, extract_unified_diff_targets
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path


def benchmark_mutation_targets(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
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


def benchmark_post_write_python_validation(
    runner: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    if result.get("is_error"):
        return result
    if not runner.benchmark_config.enabled or tool_name not in {"Write", "Patch"}:
        return result

    targets = benchmark_mutation_targets(tool_name, tool_input)
    py_targets = []
    for target in targets:
        normalized = str(target or "").replace("\\", "/").lstrip("./")
        if not normalized.endswith(".py"):
            continue
        abs_target = (runner.repo / normalized).resolve()
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


def validate_benchmark_mutation(runner: Any, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    config = runner.benchmark_config
    if not config.enabled or tool_name not in {"Write", "Patch"}:
        return None
    targets = benchmark_mutation_targets(tool_name, tool_input)
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


def parse_benchmark_denial_message(message: str) -> tuple[str, str | None]:
    reason = "policy_denied"
    path: str | None = None
    for part in message.split():
        if part.startswith("reason="):
            reason = part.split("=", 1)[1]
        if part.startswith("path="):
            path = part.split("=", 1)[1]
    return reason, path


def benchmark_denial_feedback(runner: Any, denial_message: str, paths: list[str]) -> str:
    reason, parsed_path = parse_benchmark_denial_message(denial_message)
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
