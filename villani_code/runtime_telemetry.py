from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import time
from typing import Any


@dataclass(slots=True)
class RuntimeTelemetryState:
    patch_attempts: int = 0
    retries_after_failure: int = 0
    recovery_attempted: bool = False
    recovery_success: bool | None = None
    recovered_after_failed_attempt: bool = False
    first_pass_success: bool | None = None
    verification_attempt_count: int = 0
    verifier_commands_run: list[str] = field(default_factory=list)
    first_edited_file: str = ""
    first_edited_file_authority_tier: int | None = None
    branch_count: int = 0
    selected_branch: str = ""
    no_patch_reason: str = ""
    termination_reason: str = ""
    repair_classification: str = ""
    time_to_first_edit: float | None = None
    time_to_first_verification: float | None = None
    had_failed_verification: bool = False
    last_verification_passed: bool | None = None
    repair_patch_cycles: int = 0
    runtime_started_at: float = field(default_factory=time.monotonic)

    def observe(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type", ""))
        now = time.monotonic()
        if etype == "tool_finished" and event.get("name") in {"Write", "Patch"} and not event.get("is_error"):
            self.patch_attempts += 1
            if not self.first_edited_file:
                file_path = str(event.get("input", {}).get("file_path", "")).replace("\\", "/").lstrip("./")
                self.first_edited_file = file_path
                self.time_to_first_edit = now - self.runtime_started_at
        elif etype == "benchmark_first_edit_recorded":
            if not self.first_edited_file:
                self.first_edited_file = str(event.get("path", ""))
                self.time_to_first_edit = now - self.runtime_started_at
            if self.first_edited_file_authority_tier is None:
                tier = event.get("authority_tier")
                self.first_edited_file_authority_tier = int(tier) if isinstance(tier, int) else None
        elif etype == "validation_step_started":
            command = str(event.get("command", "")).strip()
            if command:
                self.verifier_commands_run.append(command)
        elif etype == "verification_ran":
            self.verification_attempt_count += 1
            if self.time_to_first_verification is None:
                self.time_to_first_verification = now - self.runtime_started_at
        elif etype == "validation_completed":
            passed = bool(event.get("passed"))
            self.last_verification_passed = passed
            if not passed:
                self.had_failed_verification = True
        elif etype == "repair_mode_entered":
            self.recovery_attempted = True
            self.repair_classification = str(event.get("repair_classification", "")).strip()
        elif etype == "repair_patch_cycle_completed":
            if event.get("produced_patch"):
                self.repair_patch_cycles += 1
                self.retries_after_failure = self.repair_patch_cycles
            elif not self.no_patch_reason:
                self.no_patch_reason = str(event.get("reason", "")).strip()
        elif etype == "repair_branching_started":
            self.branch_count = int(event.get("branch_count", 0) or 0)
        elif etype == "repair_attempt_result" and event.get("status") == "recovered":
            self.selected_branch = str(event.get("branch_name", "")).strip()
            self.recovery_success = True
        elif etype == "repair_mode_completed":
            recovered = bool(event.get("recovered"))
            self.recovery_success = recovered if self.recovery_attempted else None
            self.branch_count = int(event.get("branch_count", self.branch_count) or self.branch_count)
            if not self.repair_classification:
                self.repair_classification = str(event.get("repair_classification", "")).strip()
        elif etype == "runner_terminated":
            self.termination_reason = str(event.get("reason", "")).strip()

    def finalize(self, *, completed: bool, terminated_reason: str) -> dict[str, Any]:
        self.termination_reason = terminated_reason
        if self.recovery_success is None and self.recovery_attempted:
            self.recovery_success = False
        if self.last_verification_passed is not None:
            self.first_pass_success = bool(self.last_verification_passed and not self.recovery_attempted and not self.had_failed_verification)
        self.recovered_after_failed_attempt = bool(self.had_failed_verification and self.recovery_success and self.retries_after_failure > 0)
        return {
            **asdict(self),
            "completed": completed,
            "verifier_commands_run": list(self.verifier_commands_run),
        }


def write_runtime_event(repo: Path, event: dict[str, Any]) -> None:
    target = repo / ".villani_code" / "runtime_events.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(event)
    payload.setdefault("ts", time.time())
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
