from __future__ import annotations


def allow_scope_expansion(current_level: str, evidence_score: float) -> tuple[bool, str]:
    if evidence_score < 0.65:
        return False, current_level
    if current_level == "symbol":
        return True, "file"
    if current_level == "file":
        return True, "adjacent_file"
    if current_level == "adjacent_file":
        return True, "two_files"
    return False, current_level
