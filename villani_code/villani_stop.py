from __future__ import annotations

from dataclasses import dataclass

from villani_code.villani_state import WorkspaceBeliefState
from villani_code.villani_validation import is_artifact_producing_task


@dataclass(slots=True)
class StopDecision:
    should_stop: bool
    reason: str


def should_stop(beliefs: WorkspaceBeliefState) -> StopDecision:
    has_validation = any(v.exit_code == 0 for v in beliefs.validation_observations) or beliefs.last_validation_passed
    no_critical = not beliefs.unresolved_critical_issues
    low_new_value = len(beliefs.recent_meaningful_changes) <= 1
    needs_artifact_gate = is_artifact_producing_task(beliefs.objective)

    if needs_artifact_gate and not beliefs.last_validation_attempted:
        return StopDecision(False, "Continue: artifact task has no deliverable validation yet.")
    if needs_artifact_gate and not beliefs.last_validation_passed:
        return StopDecision(False, "Continue: deliverable validation has not passed.")

    if (
        beliefs.materially_satisfied
        and has_validation
        and no_critical
        and beliefs.completion_confidence >= 0.8
        and low_new_value
    ):
        return StopDecision(True, "objective_validated")
    return StopDecision(False, "Continue: more evidence or repair is needed.")
