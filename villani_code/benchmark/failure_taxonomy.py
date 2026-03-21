from __future__ import annotations

from typing import TYPE_CHECKING

from villani_code.benchmark.models import FailureReason, FailureTaxonomy, TaskFamily

if TYPE_CHECKING:
    from villani_code.benchmark.models import BenchmarkCategory

_DIRECT_REASON_MAP: dict[FailureReason, FailureTaxonomy] = {
    FailureReason.TIMEOUT: FailureTaxonomy.TIMEOUT,
    FailureReason.FORBIDDEN_EDIT: FailureTaxonomy.FORBIDDEN_EDIT,
    FailureReason.INSPECT_ONLY_VIOLATION: FailureTaxonomy.INSPECT_ONLY_VIOLATION,
    FailureReason.MISSING_ARTIFACT: FailureTaxonomy.MISSING_ARTIFACT,
    FailureReason.AGENT_CRASH: FailureTaxonomy.AGENT_CRASH,
    FailureReason.VERIFIER_CRASH: FailureTaxonomy.VERIFIER_CRASH,
    FailureReason.VERIFICATION_COMMAND_FAILED_TO_LAUNCH: FailureTaxonomy.VERIFIER_CRASH,
    FailureReason.ENVIRONMENT_FAILURE: FailureTaxonomy.ENVIRONMENT_FAILURE,
    FailureReason.NO_PROGRESS: FailureTaxonomy.NO_PROGRESS,
    FailureReason.BENCHMARK_NO_PATCH_ATTEMPT: FailureTaxonomy.NO_PROGRESS,
    FailureReason.BENCHMARK_ERROR: FailureTaxonomy.BENCHMARK_ERROR,
}

# Keep thresholds few and explicit; these are benchmark-oriented heuristics for broad,
# non-productive exploration rather than task-specific rules.
_LOST_COMMAND_THRESHOLD = 12
_LOST_READ_THRESHOLD = 20
_OVER_EDITED_UNEXPECTED_PATH_THRESHOLD = 2
_OVER_EDITED_FILE_TOUCH_THRESHOLD = 4
_SYNTAX_MARKERS = (
    "syntaxerror",
    "indentationerror",
    "taberror",
    "invalid syntax",
    "unterminated string",
    "expected an indented block",
    "can't parse",
    "parseerror",
    "compileerror",
)
_COMMAND_HEAVY_TOKENS = (
    "config",
    "tooling",
    "repair",
    "refactor",
    "diagnosis",
    "debug",
    "terminal",
    "cli",
)


def _compact(text: str | None) -> str:
    return " ".join((text or "").split())


def _has_syntax_signal(chunks: list[str]) -> bool:
    haystack = "\n".join(chunk.lower() for chunk in chunks if chunk)
    return any(marker in haystack for marker in _SYNTAX_MARKERS)


def _task_requires_commands(
    *,
    task_family: TaskFamily | None,
    task_type: str | None,
    benchmark_category: "BenchmarkCategory | None" = None,
) -> bool:
    if task_family is TaskFamily.TERMINAL_WORKFLOW:
        return True
    category_value = getattr(benchmark_category, "value", str(benchmark_category or "")).lower()
    task_type_value = (task_type or "").lower()
    if category_value in {"config_tooling_repair", "failing_test_diagnosis", "refactor"}:
        return True
    return any(token in task_type_value for token in _COMMAND_HEAVY_TOKENS)


def _choose(
    taxonomy: FailureTaxonomy,
    detail: str | None,
) -> tuple[FailureTaxonomy, str | None]:
    return taxonomy, _compact(detail) or None


def classify_failure_taxonomy(
    *,
    success: bool | int,
    failure_reason: FailureReason | None,
    visible_pass: bool | None = None,
    hidden_pass: bool | None = None,
    verification_output: str | None = None,
    error: str | None = None,
    stderr_preview: str | None = None,
    num_shell_commands: int | None = None,
    file_reads: int | None = None,
    patch_attempts: int | None = None,
    files_touched: int | None = None,
    meaningful_touched_paths: list[str] | None = None,
    meaningful_expected_paths: list[str] | None = None,
    meaningful_unexpected_paths: list[str] | None = None,
    touched_file_paths: list[str] | None = None,
    expected_files: list[str] | None = None,
    touched_unexpected_files: bool | None = None,
    unrelated_file_touch: bool | None = None,
    verification_relevant: bool | None = None,
    task_family: TaskFamily | None = None,
    task_type: str | None = None,
    benchmark_category: "BenchmarkCategory | None" = None,
) -> tuple[FailureTaxonomy, str | None]:
    if bool(success):
        return _choose(FailureTaxonomy.SUCCESS, None)

    if failure_reason in _DIRECT_REASON_MAP:
        detail = None
        if failure_reason is FailureReason.BENCHMARK_NO_PATCH_ATTEMPT:
            detail = "no meaningful patch attempt recorded"
        return _choose(_DIRECT_REASON_MAP[failure_reason], detail)

    evidence_chunks = [verification_output or "", error or "", stderr_preview or ""]
    if _has_syntax_signal(evidence_chunks):
        source = "verification output" if verification_output else "execution output"
        return _choose(FailureTaxonomy.SYNTAX_BREAKAGE, f"syntax error detected in {source}")

    if bool(visible_pass) and hidden_pass is False:
        return _choose(FailureTaxonomy.PARTIAL_FIX_MISSED_ACCEPTANCE, "hidden verification failed after visible verification passed")

    expected = set(expected_files or [])
    expected_touched = set(meaningful_expected_paths or [])
    unexpected_touched = list(meaningful_unexpected_paths or [])
    meaningful_touched = list(meaningful_touched_paths or [])
    touched = list(touched_file_paths or [])

    if unexpected_touched:
        if (
            len(unexpected_touched) >= _OVER_EDITED_UNEXPECTED_PATH_THRESHOLD
            or (files_touched or 0) >= _OVER_EDITED_FILE_TOUCH_THRESHOLD
            or (expected_touched and len(unexpected_touched) >= 1)
        ):
            return _choose(
                FailureTaxonomy.OVER_EDITED,
                f"touched {len(unexpected_touched)} meaningful unexpected path(s)",
            )
        if expected and not expected_touched:
            return _choose(FailureTaxonomy.EDITED_WRONG_FILE, "touched files did not intersect expected target paths")

    if (unrelated_file_touch or bool(touched_unexpected_files)) and expected and not expected_touched and (meaningful_touched or touched):
        return _choose(FailureTaxonomy.EDITED_WRONG_FILE, "edits appear outside expected target area")

    if num_shell_commands == 0 and _task_requires_commands(task_family=task_family, task_type=task_type, benchmark_category=benchmark_category):
        return _choose(FailureTaxonomy.FAILED_TO_RUN_CORRECT_COMMAND, f"no shell commands executed on {task_type or task_family.value if task_family else 'task'} task")

    if (
        (num_shell_commands or 0) >= _LOST_COMMAND_THRESHOLD
        or (file_reads or 0) >= _LOST_READ_THRESHOLD
    ) and (patch_attempts or 0) == 0 and (files_touched or 0) <= 1:
        detail_parts: list[str] = []
        if (num_shell_commands or 0) >= _LOST_COMMAND_THRESHOLD:
            detail_parts.append(f"{num_shell_commands} shell commands")
        if (file_reads or 0) >= _LOST_READ_THRESHOLD:
            detail_parts.append(f"{file_reads} file reads")
        return _choose(FailureTaxonomy.GOT_LOST_IN_REPO, ", ".join(detail_parts) + " with no meaningful patch")

    if failure_reason in {
        FailureReason.VISIBLE_VERIFICATION_FAILED,
        FailureReason.HIDDEN_VERIFICATION_FAILED,
        FailureReason.INVALID_REPRO_TEST,
    }:
        if num_shell_commands == 0 and verification_relevant and _task_requires_commands(task_family=task_family, task_type=task_type, benchmark_category=benchmark_category):
            return _choose(FailureTaxonomy.FAILED_TO_RUN_CORRECT_COMMAND, "verification failed without any shell command activity")

    return _choose(FailureTaxonomy.UNKNOWN_FAILURE, None)
