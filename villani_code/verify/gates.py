from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GateResult:
    hard_fail: bool
    reason: str = ""


def hard_fail_gate(stage_outputs: dict[str, object]) -> GateResult:
    if not stage_outputs.get("patch_applies", True):
        return GateResult(True, "patch_apply_failed")
    if not stage_outputs.get("syntax_ok", True):
        return GateResult(True, "syntax_break")
    if not stage_outputs.get("imports_ok", True):
        return GateResult(True, "import_break")
    if stage_outputs.get("forbidden_path", False):
        return GateResult(True, "forbidden_path")
    if stage_outputs.get("stale_verification", False):
        return GateResult(True, "repeated_stale_verification")
    return GateResult(False, "")
