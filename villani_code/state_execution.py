from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from villani_code.evidence import normalize_artifact, parse_command_evidence
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path


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
        for record in parse_command_evidence(str(tool_result.get("content", ""))):
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


def collect_structured_command_results(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tool_results = list(transcript.get("tool_results", []) or [])
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "") or "")
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = None
        if not isinstance(parsed, dict):
            continue
        cmd = str(parsed.get("command", "")).strip()
        if not cmd:
            continue
        out.append(
            {
                "command": cmd,
                "exit": int(parsed.get("exit_code", parsed.get("exit", 1)) or 1),
                "stdout": str(parsed.get("stdout", ""))[:4000],
                "stderr": str(parsed.get("stderr", ""))[:4000],
                "timed_out": bool(parsed.get("timed_out", False)),
            }
        )
    return out
