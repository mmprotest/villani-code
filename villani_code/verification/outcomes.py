from __future__ import annotations

from typing import Any

from villani_code.autonomy import contract_allows_edits


def classify_node_outcome(contract_type: str, static_result: dict[str, Any], command_results: list[dict[str, Any]], changed_files: list[str], prose_only: bool = False) -> dict[str, Any]:
    command_fail = any(int(r.get("exit", 0)) != 0 for r in command_results)
    repeated = any(bool(r.get("repeated_failure")) for r in command_results)
    allows_edits = contract_allows_edits(contract_type)
    patch_exists = bool(changed_files)

    status = "partial"
    if prose_only:
        status = "stale"
    elif command_fail:
        status = "failed"
    elif allows_edits and patch_exists and static_result.get("meaningful_patch", False):
        status = "passed"
    elif not allows_edits and (command_results or static_result.get("findings") == []):
        status = "passed"

    return {
        "status": status,
        "patch_exists": patch_exists,
        "same_failure_repeated": repeated,
        "validation_worsened": command_fail and patch_exists,
        "suspicious_breadth": bool(static_result.get("suspicious_breadth")),
        "patch_no_improvement": patch_exists and command_fail,
        "tool_denied": False,
        "prose_only": prose_only,
    }
