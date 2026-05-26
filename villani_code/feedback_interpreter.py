from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from villani_code.task_contract import ContractCheckResult


@dataclass(slots=True)
class FeedbackInterpretation:
    failed: bool
    failed_check: str
    disproved_assumption: str
    unsatisfied_contract_items: list[str] = field(default_factory=list)
    likely_next_action: str = "inspect_failure_excerpt"
    evidence_excerpt: str = ""


def _compact_excerpt(*chunks: str, limit: int = 220) -> str:
    for chunk in chunks:
        text = str(chunk or "").strip()
        if text:
            return text[:limit]
    return ""


def _extract_traceback_path(command_results: list[dict[str, Any]]) -> str:
    pattern = re.compile(r'File "([^"]+)", line \d+')
    fallback = re.compile(r'([\w./\\-]+\.py)')
    for result in command_results:
        text = "\n".join([str(result.get("stdout", "")), str(result.get("stderr", ""))])
        found = pattern.search(text)
        if found:
            return str(found.group(1)).replace("\\", "/").lstrip("./")
        found = fallback.search(text)
        if found:
            return str(found.group(1)).replace("\\", "/").lstrip("./")
    return ""


def interpret_feedback(
    command_results: list[dict[str, Any]],
    contract_result: ContractCheckResult | None,
    changed_files: list[str],
) -> FeedbackInterpretation:
    failed_commands = [r for r in command_results if int(r.get("exit", 0)) != 0]
    has_missing_observable = bool(
        contract_result
        and any(str(f.category).startswith("missing_") for f in contract_result.findings)
    )
    failed = bool(failed_commands or has_missing_observable)

    failed_check = "none"
    disproved_assumption = "validation_succeeded"
    evidence_excerpt = ""

    if failed_commands:
        failed_check = str(failed_commands[0].get("command", "validation_command")) or "validation_command"
        disproved_assumption = "latest_change_preserves_validation"
        evidence_excerpt = _compact_excerpt(
            str(failed_commands[0].get("stderr", "")),
            str(failed_commands[0].get("stdout", "")),
        )
    elif has_missing_observable:
        failed_check = "task_outcome_contract.missing_evidence"
        disproved_assumption = "required_observables_were_produced"
        first = next((f for f in (contract_result.findings if contract_result else []) if str(f.category).startswith("missing_")), None)
        evidence_excerpt = _compact_excerpt(first.message if first else "")

    unsatisfied_contract_items = []
    if contract_result:
        for finding in contract_result.findings:
            unsatisfied_contract_items.append(f"{finding.category}:{finding.path or '-'}:{finding.message}")

    traceback_path = _extract_traceback_path(failed_commands or command_results)
    repeated_failures = len({str(r.get('command', '')) for r in failed_commands}) == 1 and len(failed_commands) >= 2

    if traceback_path:
        likely_next_action = "inspect_or_patch_traceback_target"
        if not evidence_excerpt:
            evidence_excerpt = traceback_path
    elif has_missing_observable:
        likely_next_action = "produce_or_verify_required_observable"
    elif repeated_failures:
        likely_next_action = "change_validation_strategy_or_inspect_diff"
    else:
        likely_next_action = "inspect_failure_excerpt"

    if not changed_files and failed:
        disproved_assumption = "no_change_needed_before_validation"

    return FeedbackInterpretation(
        failed=failed,
        failed_check=failed_check,
        disproved_assumption=disproved_assumption,
        unsatisfied_contract_items=unsatisfied_contract_items,
        likely_next_action=likely_next_action,
        evidence_excerpt=evidence_excerpt,
    )
