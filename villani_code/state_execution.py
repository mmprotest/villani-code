from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from villani_code.evidence import normalize_artifact, parse_command_evidence
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path


def _has_masking_pattern(command: str) -> bool:
    lowered = str(command or "").lower()
    return ("||" in lowered) or ("&& echo" in lowered) or ("2>&1 |" in lowered) or ("timeout" in lowered and "echo" in lowered)


@dataclass(frozen=True, slots=True)
class ChangeSummary:
    intentional: list[str]
    incidental: list[str]
    all_changes: list[str]


def summarize_changes(changed_files: list[str]) -> ChangeSummary:
    intentional: list[str] = []
    incidental: list[str] = []
    for path in changed_files:
        if is_ignored_repo_path(path) or classify_repo_path(path) != "authoritative":
            incidental.append(path)
        else:
            intentional.append(path)
    all_changes = sorted(set(intentional) | set(incidental))
    return ChangeSummary(
        intentional=sorted(set(intentional)),
        incidental=sorted(set(incidental)),
        all_changes=all_changes,
    )


def collect_validation_artifacts(transcript: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []
    for tool_result in transcript.get("tool_results", []):
        if bool(tool_result.get("is_error")):
            continue
        for record in parse_command_evidence(str(tool_result.get("content", ""))):
            if _has_masking_pattern(str(record.get("command", ""))):
                continue
            artifact = normalize_artifact(record)
            if artifact:
                artifacts.append(artifact)
    return artifacts


def collect_runner_failures(transcript: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for tool_result in transcript.get("tool_results", []):
        if tool_result.get("is_error"):
            failures.append(f"tool_failure: {str(tool_result.get('content', ''))[:220]}")
    return failures
