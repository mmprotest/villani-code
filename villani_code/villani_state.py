from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ValidationObservation:
    command: str
    exit_code: int
    source: str = "command"


@dataclass(slots=True)
class FailureObservation:
    signature: str
    detail: str
    source: str
    is_critical: bool = True


@dataclass(slots=True)
class ActionResultSummary:
    action_kind: str
    success: bool
    changed_files: list[str] = field(default_factory=list)
    validation_observations: list[ValidationObservation] = field(default_factory=list)
    failures: list[FailureObservation] = field(default_factory=list)
    notes: str = ""


@dataclass(slots=True)
class WorkspaceBeliefState:
    objective: str
    workspace_summary: str = ""
    artifact_inventory: list[str] = field(default_factory=list)
    likely_deliverables: list[str] = field(default_factory=list)
    runnable_entrypoints: list[str] = field(default_factory=list)
    test_inventory: list[str] = field(default_factory=list)
    validation_observations: list[ValidationObservation] = field(default_factory=list)
    known_failures: list[FailureObservation] = field(default_factory=list)
    scratch_artifacts: list[str] = field(default_factory=list)
    recent_meaningful_changes: list[str] = field(default_factory=list)
    repeated_patterns: list[str] = field(default_factory=list)
    completion_confidence: float = 0.0
    last_action_result: ActionResultSummary | None = None
    materially_satisfied: bool = False
    unresolved_critical_issues: list[str] = field(default_factory=list)
    action_history: list[ActionResultSummary] = field(default_factory=list)

    def add_action_result(self, result: ActionResultSummary) -> None:
        self.last_action_result = result
        self.action_history.append(result)
        self.action_history = self.action_history[-10:]

    def to_snapshot(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["completion_confidence"] = round(float(self.completion_confidence), 3)
        return payload


BELIEF_PATH = Path(".villani") / "villani_beliefs.json"


def save_beliefs(repo: Path, beliefs: WorkspaceBeliefState) -> Path:
    target = repo / BELIEF_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(beliefs.to_snapshot(), indent=2), encoding="utf-8")
    return target
