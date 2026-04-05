from __future__ import annotations

import difflib
import py_compile
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from villani_code.patch_apply import PatchApplyError, extract_unified_diff_targets, parse_unified_diff
from villani_code.permissions import Decision
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.tools import execute_tool


_FENCED_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)
_DIFF_HEADER_RE = re.compile(r"^(diff --git\s+|---\s+|\+\+\+\s+)", re.MULTILINE)


@dataclass(frozen=True)
class MutationGuardThresholds:
    max_touched_lines: int = 120
    max_touched_ratio: float = 0.35
    min_lines_for_ratio_guard: int = 40


MUTATION_GUARD_THRESHOLDS = MutationGuardThresholds()


@dataclass
class MutationEditAnalysis:
    path: str
    existed: bool
    original_line_count: int
    new_line_count: int
    lines_added: int
    lines_deleted: int
    touched_lines: int
    touched_ratio: float
    probable_rewrite: bool


def _extract_fenced_code(text: str) -> str | None:
    match = _FENCED_BLOCK_RE.search(text)
    if not match:
        return None
    return match.group(1).strip("\n")


def _extract_fenced_blocks(text: str) -> list[str]:
    return [m.group(1).strip("\n") for m in _FENCED_BLOCK_RE.finditer(text)]


def _looks_like_unified_diff(text: str) -> bool:
    return bool(_DIFF_HEADER_RE.search(text) and "@@ " in text)


def _extract_diff_text_from_payload(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return raw
    for block in _extract_fenced_blocks(raw):
        if _looks_like_unified_diff(block):
            return block + ("" if block.endswith("\n") else "\n")
    if _looks_like_unified_diff(raw):
        return raw
    start = None
    lines = raw.replace("\r\n", "\n").split("\n")
    for idx, line in enumerate(lines):
        if line.startswith("diff --git ") or line.startswith("--- "):
            start = idx
            break
    if start is not None:
        extracted = "\n".join(lines[start:]).strip("\n")
        if extracted:
            return extracted + "\n"
    return raw


def _extract_literal_content_from_payload(content: str) -> str:
    blocks = _extract_fenced_blocks(content)
    if len(blocks) == 1:
        extracted = blocks[0]
        return extracted + ("" if extracted.endswith("\n") else "\n")
    return content


def _analyze_text_rewrite(
    path: str, existed: bool, before_text: str, after_text: str, thresholds: MutationGuardThresholds
) -> MutationEditAnalysis:
    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    lines_added = 0
    lines_deleted = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            lines_added += j2 - j1
        elif tag == "delete":
            lines_deleted += i2 - i1
        elif tag == "replace":
            lines_deleted += i2 - i1
            lines_added += j2 - j1
    touched = lines_added + lines_deleted
    baseline = max(len(before_lines), 1)
    touched_ratio = touched / baseline
    probable_rewrite = existed and (
        touched > thresholds.max_touched_lines
        or (len(before_lines) >= thresholds.min_lines_for_ratio_guard and touched_ratio > thresholds.max_touched_ratio)
    )
    return MutationEditAnalysis(
        path=path,
        existed=existed,
        original_line_count=len(before_lines),
        new_line_count=len(after_lines),
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        touched_lines=touched,
        touched_ratio=touched_ratio,
        probable_rewrite=probable_rewrite,
    )


def _analyze_patch_mutation(
    repo: Path, unified_diff: str, default_file_path: str | None, thresholds: MutationGuardThresholds
) -> list[MutationEditAnalysis]:
    analyses: list[MutationEditAnalysis] = []
    parsed = parse_unified_diff(unified_diff)
    for target in extract_unified_diff_targets(unified_diff, default_file_path=default_file_path):
        normalized = str(target).replace("\\", "/").lstrip("./")
        path = (repo / normalized).resolve()
        existed = path.exists()
        before_text = path.read_text(encoding="utf-8", errors="replace") if existed and path.is_file() else ""
        added = 0
        deleted = 0
        for file_patch in parsed:
            file_target = (
                file_patch.new_path
                if file_patch.new_path != "/dev/null"
                else file_patch.old_path
            ).replace("\\", "/").lstrip("./")
            if file_target != normalized:
                continue
            for hunk in file_patch.hunks:
                for hline in hunk.lines:
                    if hline.startswith("+"):
                        added += 1
                    elif hline.startswith("-"):
                        deleted += 1
        baseline = max(len(before_text.splitlines()), 1)
        touched = added + deleted
        touched_ratio = touched / baseline
        analyses.append(
            MutationEditAnalysis(
                path=normalized,
                existed=existed,
                original_line_count=len(before_text.splitlines()),
                new_line_count=max(0, len(before_text.splitlines()) - deleted + added),
                lines_added=added,
                lines_deleted=deleted,
                touched_lines=touched,
                touched_ratio=touched_ratio,
                probable_rewrite=existed
                and (
                    touched > thresholds.max_touched_lines
                    or (
                        len(before_text.splitlines()) >= thresholds.min_lines_for_ratio_guard
                        and touched_ratio > thresholds.max_touched_ratio
                    )
                ),
            )
        )
    return analyses


def _sanitize_tool_input_file_path(tool_input: dict[str, Any], repo: Path) -> None:
    """Normalize and sanitize a `file_path` value in tool_input in-place.

    - Strip surrounding quotes
    - Normalize backslashes to forward slashes
    - If an absolute path points inside `repo`, convert to a repo-relative path
    - Strip leading './' or leading '/'
    """
    try:
        raw = tool_input.get("file_path")
        if not isinstance(raw, str):
            return
        fp = raw.strip()
        if (fp.startswith('"') and fp.endswith('"')) or (fp.startswith("'") and fp.endswith("'")):
            fp = fp[1:-1].strip()
        fp = fp.strip('"').strip("'")
        fp = fp.replace("\\", "/")
        p = Path(fp)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(repo.resolve())
                fp = str(rel).replace("\\", "/")
            except Exception:
                pass
        fp = fp.lstrip("./")
        if fp.startswith("/"):
            fp = fp.lstrip("/")
        tool_input["file_path"] = fp
    except Exception:
        return


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


def _normalize_mutation_payload(tool_name: str, tool_input: dict[str, Any]) -> None:
    if tool_name == "Write":
        content = str(tool_input.get("content", ""))
        if "```" in content:
            tool_input["content"] = _extract_literal_content_from_payload(content)
        return
    if tool_name == "Patch":
        diff = str(tool_input.get("unified_diff", ""))
        normalized = _extract_diff_text_from_payload(diff)
        if normalized != diff:
            tool_input["unified_diff"] = normalized


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


def _reject_rewrite_message(analysis: MutationEditAnalysis) -> str:
    percent = int(round(analysis.touched_ratio * 100))
    return (
        "Rewrite-heavy mutation rejected for existing file "
        f"{analysis.path}: touched_lines={analysis.touched_lines} "
        f"(+{analysis.lines_added}/-{analysis.lines_deleted}), touched_ratio={percent}%. "
        "Emit a narrow Patch targeting only the smallest necessary region; avoid whole-file rewrites."
    )


def _prepare_global_mutation_policy(
    runner: Any, tool_name: str, tool_input: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if tool_name == "Write":
        file_path = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
        if not file_path:
            return tool_name, tool_input, None
        path = (runner.repo / file_path).resolve()
        exists = path.exists() and path.is_file()
        if not exists:
            return tool_name, tool_input, None
        before_text = path.read_text(encoding="utf-8", errors="replace")
        after_text = str(tool_input.get("content", ""))
        if before_text == after_text:
            return tool_name, tool_input, {"content": f"No changes for {file_path}", "is_error": False}
        analysis = _analyze_text_rewrite(file_path, True, before_text, after_text, MUTATION_GUARD_THRESHOLDS)
        if analysis.probable_rewrite:
            return tool_name, tool_input, {"content": _reject_rewrite_message(analysis), "is_error": True}
        diff_lines = list(
            difflib.unified_diff(
                before_text.splitlines(),
                after_text.splitlines(),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                lineterm="",
            )
        )
        unified_diff = "\n".join(diff_lines).rstrip("\n") + "\n"
        patched_input = {"file_path": file_path, "unified_diff": unified_diff}
        return "Patch", patched_input, None
    if tool_name == "Patch":
        default_path = str(tool_input.get("file_path", "") or "") or None
        try:
            analyses = _analyze_patch_mutation(
                runner.repo, str(tool_input.get("unified_diff", "")), default_path, MUTATION_GUARD_THRESHOLDS
            )
        except PatchApplyError:
            return tool_name, tool_input, None
        for analysis in analyses:
            if analysis.probable_rewrite:
                return tool_name, tool_input, {"content": _reject_rewrite_message(analysis), "is_error": True}
    return tool_name, tool_input, None


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
    try:
        _sanitize_tool_input_file_path(tool_input, runner.repo)
    except Exception:
        pass

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
    if tool_name in {"Write", "Patch"}:
        _normalize_mutation_payload(tool_name, tool_input)

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
        if getattr(runner, "auto_approve", False):
            runner.event_callback(
                {
                    "type": "approval_auto_resolved",
                    "name": tool_name,
                    "input": tool_input,
                    "approved": True,
                    "approval_mode": "auto",
                    "decision_source": "auto_approve_flag",
                }
            )
        elif runner.villani_mode:
            runner.event_callback(
                {
                    "type": "approval_auto_resolved",
                    "name": tool_name,
                    "input": tool_input,
                    "approved": True,
                    "approval_mode": "auto",
                    "decision_source": "villani_mode",
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
            approved = runner.approval_callback(tool_name, tool_input)
            runner.event_callback(
                {
                    "type": "approval_resolved",
                    "name": tool_name,
                    "input": tool_input,
                    "approved": approved,
                }
            )
            if not approved:
                return {"content": "User denied tool execution", "is_error": True}
    elif runner.plan_mode != "off" and tool_name in {"Write", "Patch"}:
        return {"content": "Plan mode: edit not executed", "is_error": False}

    tool_name, tool_input, forced_result = _prepare_global_mutation_policy(runner, tool_name, tool_input)
    if forced_result is not None:
        return forced_result

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
        targets = _benchmark_mutation_targets(tool_name, tool_input)
        normalized_targets = sorted(
            {
                str(target or "").replace("\\", "/").lstrip("./")
                for target in targets
                if str(target or "").strip()
            }
        )
        if normalized_targets:
            runner._intended_targets.update(normalized_targets)
            runner._current_verification_targets = set(normalized_targets)
            runner._current_verification_before_contents = {}
            checkpoint_paths: list[Path] = []
            for normalized_target in normalized_targets:
                target_path = (runner.repo / normalized_target).resolve()
                checkpoint_paths.append(Path(normalized_target))
                if target_path.exists() and target_path.is_file():
                    before_text = target_path.read_text(encoding="utf-8", errors="replace")
                    runner._before_contents[normalized_target] = before_text
                    runner._current_verification_before_contents[normalized_target] = before_text
            runner.checkpoints.create(checkpoint_paths, message_index=message_count)
    runner.event_callback(
        {
            "type": "tool_started",
            "name": tool_name,
            "input": tool_input,
            "tool_use_id": tool_use_id,
        }
    )
    result = execute_tool(
        tool_name,
        tool_input,
        runner.repo,
        unsafe=runner.unsafe,
        debug_callback=getattr(runner, "_debug_tool_callback", None),
        tool_call_id=tool_use_id,
    )
    return _benchmark_post_write_python_validation(runner, tool_name, tool_input, result)
