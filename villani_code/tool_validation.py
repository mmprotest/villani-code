from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.patch_apply import PatchApplyError, parse_unified_diff
from villani_code.shells import classify_and_rewrite_command, detect_shell_environment


class ToolValidationError(Exception):
    def __init__(self, reason_code: str, reason: str, details: dict[str, Any] | None = None, fingerprint: str = "") -> None:
        super().__init__(reason)
        self.reason_code = reason_code
        self.reason = reason
        self.details = details or {}
        self.fingerprint = fingerprint


@dataclass(frozen=True)
class ToolValidationResult:
    valid: bool
    reason_code: str = ""
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    @classmethod
    def ok(cls) -> "ToolValidationResult":
        return cls(valid=True)

    @classmethod
    def rejected(
        cls,
        *,
        reason_code: str,
        reason: str,
        details: dict[str, Any] | None = None,
        fingerprint: str = "",
    ) -> "ToolValidationResult":
        return cls(
            valid=False,
            reason_code=reason_code,
            reason=reason,
            details=details or {},
            fingerprint=fingerprint,
        )


class ToolCallValidator:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def validate(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        shell_environment: dict[str, Any] | None = None,
    ) -> ToolValidationResult:
        if tool_name == "Write":
            return self._validate_write(tool_input)
        if tool_name == "Patch":
            return self._validate_patch(tool_input)
        if tool_name == "Bash":
            return self._validate_bash(tool_input, shell_environment=shell_environment)
        return ToolValidationResult.ok()

    def _validate_write(self, tool_input: dict[str, Any]) -> ToolValidationResult:
        file_path = str(tool_input.get("file_path", "")).strip()
        if not file_path:
            return ToolValidationResult.rejected(
                reason_code="write_missing_file_path",
                reason="Write call rejected: file_path is required.",
                fingerprint="Write:write_missing_file_path",
            )
        if "content" not in tool_input:
            return ToolValidationResult.rejected(
                reason_code="write_missing_content",
                reason="Write call rejected: content is required.",
                fingerprint="Write:write_missing_content",
            )
        content = str(tool_input.get("content", ""))
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return ToolValidationResult.rejected(
                reason_code="write_empty_content",
                reason="Write call rejected: content must be non-empty and not whitespace-only.",
                details={"normalized_length": 0},
                fingerprint="Write:write_empty_content",
            )
        return ToolValidationResult.ok()

    def _validate_patch(self, tool_input: dict[str, Any]) -> ToolValidationResult:
        diff_text = str(tool_input.get("unified_diff", ""))
        if not diff_text.strip():
            return ToolValidationResult.rejected(
                reason_code="patch_missing_unified_diff",
                reason="Patch call rejected: unified_diff is required and cannot be empty.",
                fingerprint="Patch:patch_missing_unified_diff",
            )
        try:
            parsed = parse_unified_diff(diff_text)
        except PatchApplyError as exc:
            return ToolValidationResult.rejected(
                reason_code="patch_malformed_unified_diff",
                reason="Patch call rejected: unified_diff is malformed.",
                details={"parse_error": str(exc)},
                fingerprint="Patch:patch_malformed_unified_diff",
            )
        if not parsed:
            return ToolValidationResult.rejected(
                reason_code="patch_no_file_sections",
                reason="Patch call rejected: unified_diff does not contain any file sections.",
                fingerprint="Patch:patch_no_file_sections",
            )
        if not any(file_patch.hunks for file_patch in parsed):
            return ToolValidationResult.rejected(
                reason_code="patch_missing_hunks",
                reason="Patch call rejected: unified_diff has no hunks to apply.",
                fingerprint="Patch:patch_missing_hunks",
            )
        return ToolValidationResult.ok()

    def _validate_bash(
        self,
        tool_input: dict[str, Any],
        *,
        shell_environment: dict[str, Any] | None = None,
    ) -> ToolValidationResult:
        command = str(tool_input.get("command", ""))
        if not command.strip():
            return ToolValidationResult.rejected(
                reason_code="bash_empty_command",
                reason="Bash call rejected: command cannot be empty.",
                fingerprint="Bash:bash_empty_command",
            )

        shell_state = shell_environment if isinstance(shell_environment, dict) else {}
        shell_family = str(shell_state.get("shell_family", "")).strip()
        if not shell_family:
            shell_family = detect_shell_environment(cwd=str(self.repo)).shell_family

        decision = classify_and_rewrite_command(command, shell_family)
        if decision.classification == "blocked":
            details: dict[str, Any] = {
                "shell_family": shell_family,
                "classification": decision.classification,
            }
            if decision.short_reason:
                details["short_reason"] = decision.short_reason
            if decision.offending_token:
                details["offending_token"] = decision.offending_token
            if decision.offending_pattern:
                details["offending_pattern"] = decision.offending_pattern
            if decision.suggested_equivalent:
                details["suggested_equivalent"] = decision.suggested_equivalent
            fingerprint_suffix = decision.offending_pattern or decision.offending_token or "blocked"
            return ToolValidationResult.rejected(
                reason_code="bash_blocked_by_shell_policy",
                reason="Bash call rejected: command is incompatible with active shell constraints.",
                details=details,
                fingerprint=f"Bash:bash_blocked_by_shell_policy:{fingerprint_suffix}",
            )
        return ToolValidationResult.ok()


def render_validation_error_content(result: ToolValidationResult, retry_count: int) -> str:
    payload: dict[str, Any] = {
        "error_type": "tool_validation_error",
        "reason_code": result.reason_code,
        "reason": result.reason,
        "retry_count": retry_count,
    }
    if result.details:
        payload["details"] = result.details
    if retry_count > 1:
        payload["guidance"] = "Same invalid tool call shape repeated; change tool input structure before retrying."
    return json.dumps(payload, separators=(",", ":"))
