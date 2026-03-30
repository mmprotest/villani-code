from __future__ import annotations

from dataclasses import dataclass

from villani_code.villani_state import WorkspaceBeliefState


@dataclass(slots=True)
class StopDecision:
    should_stop: bool
    reason: str


def should_stop(beliefs: WorkspaceBeliefState) -> StopDecision:
    has_validation = any(v.exit_code == 0 for v in beliefs.validation_observations)
    no_critical = not beliefs.unresolved_critical_issues
    low_new_value = len(beliefs.recent_meaningful_changes) <= 1

    if (
        beliefs.materially_satisfied
        and has_validation
        and no_critical
        and beliefs.completion_confidence >= 0.8
        and low_new_value
    ):
        return StopDecision(True, "Objective satisfied with command-backed validation and low residual risk.")
    return StopDecision(False, "Continue: more evidence or repair is needed.")
