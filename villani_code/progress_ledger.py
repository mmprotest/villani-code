from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any
from villani_code.task_contract import TaskOutcomeContract


@dataclass(frozen=True)
class ProgressObservation:
    tool_name: str
    tool_input: dict[str, Any]
    result_is_error: bool
    action_changed_files: list[str]
    cumulative_changed_files: list[str]
    validation_artifacts: list[str]
    verification_fingerprint: str
    contract_satisfied: bool | None
    contract_findings_count: int | None


@dataclass(frozen=True)
class ProgressAssessment:
    improving: bool
    stalled: bool
    repeated_file_patch: bool
    repeated_failed_command: bool
    repeated_verification: bool
    reason: str
    suggested_recovery_mode: str


@dataclass
class ProgressLedger:
    observations: list[ProgressObservation] = field(default_factory=list)
    _same_file_patch_streak: int = 0
    _last_single_changed_file: str = ""
    _last_failed_command_sig: str = ""
    _failed_command_streak: int = 0
    _last_verification_fingerprint: str = ""
    _verification_repeat_streak: int = 0
    _prev_contract_satisfied: bool | None = None
    _prev_contract_findings_count: int | None = None
    _prev_validation_artifact_count: int = 0
    _prev_changed_file_count: int = 0
    _improved_contract_state: bool = False
    _contract_findings_improved: bool = False

    def record_observation(
        self,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
        result_is_error: bool,
        action_changed_files: list[str],
        cumulative_changed_files: list[str],
        validation_artifacts: list[str],
        verification_fingerprint: str,
        contract_satisfied: bool | None,
        contract_findings_count: int | None,
    ) -> ProgressObservation:
        observation = ProgressObservation(
            tool_name=str(tool_name or ""),
            tool_input=dict(tool_input or {}),
            result_is_error=bool(result_is_error),
            action_changed_files=sorted({str(path) for path in action_changed_files if str(path)}),
            cumulative_changed_files=sorted({str(path) for path in cumulative_changed_files if str(path)}),
            validation_artifacts=[str(v) for v in validation_artifacts],
            verification_fingerprint=str(verification_fingerprint or ""),
            contract_satisfied=contract_satisfied,
            contract_findings_count=contract_findings_count,
        )
        self.observations.append(observation)

        self._update_file_patch_streak(observation)
        self._update_failed_command_streak(observation)
        self._update_verification_repeat(observation)
        self._update_growth(observation)
        self._update_contract_improvement(observation)
        return observation

    def assess(self) -> ProgressAssessment:
        repeated_file_patch = self._same_file_patch_streak >= 3
        repeated_failed_command = self._failed_command_streak >= 2
        repeated_verification = self._verification_repeat_streak >= 2
        stalled = repeated_file_patch or repeated_failed_command or repeated_verification
        improving = self._improved_contract_state or self._contract_findings_improved

        reasons: list[str] = []
        if repeated_file_patch:
            reasons.append("same_file_patched_without_new_verification_or_contract_progress")
        if repeated_failed_command:
            reasons.append("same_failed_command_repeated")
        if repeated_verification:
            reasons.append("same_verification_fingerprint_repeated")
        if improving:
            reasons.append("contract_state_improved")
        reason = ";".join(reasons) if reasons else "stable"

        recovery = "none"
        if stalled:
            recovery = "verification" if repeated_verification else "strategy_shift"

        return ProgressAssessment(
            improving=improving,
            stalled=stalled,
            repeated_file_patch=repeated_file_patch,
            repeated_failed_command=repeated_failed_command,
            repeated_verification=repeated_verification,
            reason=reason,
            suggested_recovery_mode=recovery,
        )

    def _update_file_patch_streak(self, observation: ProgressObservation) -> None:
        single = observation.action_changed_files[0] if len(observation.action_changed_files) == 1 else ""
        has_contract_improvement = self._is_contract_improvement(observation)
        has_verification_growth = len(observation.validation_artifacts) > self._prev_validation_artifact_count
        if single and single == self._last_single_changed_file and not has_contract_improvement and not has_verification_growth:
            self._same_file_patch_streak += 1
        elif single:
            self._same_file_patch_streak = 1
        else:
            self._same_file_patch_streak = 0
        self._last_single_changed_file = single

    def _update_failed_command_streak(self, observation: ProgressObservation) -> None:
        if observation.tool_name != "Bash" or not observation.result_is_error:
            self._failed_command_streak = 0
            self._last_failed_command_sig = ""
            return
        sig = json.dumps(observation.tool_input, sort_keys=True)
        if sig and sig == self._last_failed_command_sig:
            self._failed_command_streak += 1
        else:
            self._failed_command_streak = 1
        self._last_failed_command_sig = sig

    def _update_verification_repeat(self, observation: ProgressObservation) -> None:
        fingerprint = observation.verification_fingerprint
        if not fingerprint:
            self._verification_repeat_streak = 0
            self._last_verification_fingerprint = ""
            return
        if fingerprint == self._last_verification_fingerprint:
            self._verification_repeat_streak += 1
        else:
            self._verification_repeat_streak = 1
        self._last_verification_fingerprint = fingerprint

    def _update_growth(self, observation: ProgressObservation) -> None:
        self._prev_changed_file_count = len(observation.cumulative_changed_files)
        self._prev_validation_artifact_count = len(observation.validation_artifacts)

    def _is_contract_improvement(self, observation: ProgressObservation) -> bool:
        if observation.contract_satisfied is True and self._prev_contract_satisfied is not True:
            return True
        if (
            observation.contract_findings_count is not None
            and self._prev_contract_findings_count is not None
            and observation.contract_findings_count < self._prev_contract_findings_count
        ):
            return True
        return False

    def _update_contract_improvement(self, observation: ProgressObservation) -> None:
        improved = self._is_contract_improvement(observation)
        self._improved_contract_state = improved
        self._contract_findings_improved = bool(
            observation.contract_findings_count is not None
            and self._prev_contract_findings_count is not None
            and observation.contract_findings_count < self._prev_contract_findings_count
        )
        self._prev_contract_satisfied = observation.contract_satisfied
        self._prev_contract_findings_count = observation.contract_findings_count


def format_recovery_packet(
    assessment: ProgressAssessment,
    contract: TaskOutcomeContract | None,
    last_validation_summary: str,
    changed_files: list[str],
) -> str:
    objective = (
        contract.objective.strip()
        if contract is not None and contract.objective.strip()
        else "unspecified objective"
    )
    validation = (last_validation_summary or "").strip()
    unsatisfied_items: list[str] = []
    if contract is not None:
        summary_lower = validation.lower()
        for observable in contract.required_observables:
            path = str(observable.path or "").strip()
            if path and path not in changed_files and path not in summary_lower:
                unsatisfied_items.append(f"required_observable: {observable.kind} {path}")
        for check in contract.behavioral_checks:
            command = str(check.command or "").strip()
            if command and command.lower() not in summary_lower:
                unsatisfied_items.append(f"behavioral_check: {command}")
    if not unsatisfied_items:
        unsatisfied_items.append("No explicit task outcome contract gaps identified; gather fresh validation evidence for required observables and behavioral checks.")

    changed = sorted({str(path).strip() for path in changed_files if str(path).strip()})
    stalled_signal = (
        "repeated_file_patch"
        if assessment.repeated_file_patch
        else "repeated_failed_command"
        if assessment.repeated_failed_command
        else "repeated_verification"
        if assessment.repeated_verification
        else "generic_stall"
    )
    reason = assessment.reason or "stall_detected"
    lines = [
        "<recovery_packet>",
        f"reason: {reason}",
        f"stalled_signal: {stalled_signal}",
        f"contract_objective: {objective}",
        "unsatisfied_task_outcome_contract_items:",
        *[f"- {item}" for item in unsatisfied_items[:8]],
        "changed_files:",
        *([f"- {path}" for path in changed[:12]] or ["- (none)"]),
        "required_next_action:",
        "Choose one progress recovery action:",
        "1. inspect the most relevant current diff or file",
        "2. simplify the repeated edit",
        "3. run one targeted validation command",
        "4. make one minimal patch tied to the unsatisfied contract",
        "</recovery_packet>",
    ]
    return "\n".join(lines)
