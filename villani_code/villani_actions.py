from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from villani_code.villani_state import WorkspaceBeliefState


class ActionKind(StrEnum):
    INSPECT = "inspect"
    IMPLEMENT = "implement"
    REPAIR = "repair"
    VALIDATE = "validate"
    CLEANUP = "cleanup"
    SUMMARIZE = "summarize"
    STOP = "stop"


@dataclass(slots=True)
class VillaniAction:
    kind: ActionKind
    intent: str
    rationale: str
    expected_evidence: list[str]
    target_files: list[str] = field(default_factory=list)
    priority: float = 0.5
    confidence: float = 0.5
    risk: str = "medium"


def propose_actions(beliefs: WorkspaceBeliefState) -> list[VillaniAction]:
    actions: list[VillaniAction] = []
    has_artifact = bool(beliefs.likely_deliverables)
    has_validation = bool(beliefs.validation_observations)
    has_failure = bool(beliefs.unresolved_critical_issues)
    if has_failure:
        actions.append(
            VillaniAction(
                kind=ActionKind.REPAIR,
                intent="Repair concrete failing behavior",
                rationale="Critical failures exist from command evidence.",
                expected_evidence=["failure signature changes", "validation exits 0"],
                target_files=beliefs.likely_deliverables[:5],
                priority=0.98,
                confidence=0.9,
                risk="high",
            )
        )
    if not has_artifact:
        actions.append(
            VillaniAction(
                kind=ActionKind.IMPLEMENT,
                intent="Create or improve artifact to satisfy objective",
                rationale="No clear deliverable exists yet.",
                expected_evidence=["authoritative source file changed"],
                priority=0.92,
                confidence=0.75,
            )
        )
    if has_artifact and not has_validation:
        actions.append(
            VillaniAction(
                kind=ActionKind.VALIDATE,
                intent="Run validation appropriate to changed artifacts",
                rationale="Deliverable exists but lacks command-backed validation.",
                expected_evidence=["test/build command output"],
                target_files=beliefs.test_inventory[:6],
                priority=0.94,
                confidence=0.85,
            )
        )
    if beliefs.scratch_artifacts and beliefs.completion_confidence >= 0.75:
        actions.append(
            VillaniAction(
                kind=ActionKind.CLEANUP,
                intent="Remove scratch/debug artifacts",
                rationale="Scratch files should not pollute deliverables.",
                expected_evidence=["scratch files deleted"],
                target_files=beliefs.scratch_artifacts[:8],
                priority=0.70,
                confidence=0.8,
                risk="low",
            )
        )
    if beliefs.materially_satisfied and beliefs.completion_confidence >= 0.8:
        actions.append(
            VillaniAction(
                kind=ActionKind.SUMMARIZE,
                intent="Summarize completed work and evidence",
                rationale="Objective appears satisfied with evidence.",
                expected_evidence=["final summary tied to command evidence"],
                priority=0.78,
                confidence=0.85,
                risk="low",
            )
        )
        actions.append(
            VillaniAction(
                kind=ActionKind.STOP,
                intent="Stop autonomous loop",
                rationale="Further actions likely add little value.",
                expected_evidence=["high confidence and no unresolved critical issues"],
                priority=0.76,
                confidence=0.8,
                risk="low",
            )
        )

    if not actions:
        actions.append(
            VillaniAction(
                kind=ActionKind.INSPECT,
                intent="Inspect workspace to reduce uncertainty",
                rationale="No high-confidence action available.",
                expected_evidence=["new inventory/failure data"],
                priority=0.55,
                confidence=0.5,
            )
        )
    return sorted(actions, key=lambda a: (a.priority, a.confidence), reverse=True)


def choose_best_action(candidates: list[VillaniAction]) -> VillaniAction:
    return candidates[0]
