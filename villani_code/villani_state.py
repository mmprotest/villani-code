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
    validated_artifacts: list[str] = field(default_factory=list)
    recent_meaningful_changes: list[str] = field(default_factory=list)
    repeated_patterns: list[str] = field(default_factory=list)
    repeated_action_kinds: dict[str, int] = field(default_factory=dict)
    repeated_failure_signatures: dict[str, int] = field(default_factory=dict)
    completion_confidence: float = 0.0
    last_action_result: ActionResultSummary | None = None
    materially_satisfied: bool = False
    unresolved_critical_issues: list[str] = field(default_factory=list)
    action_history: list[ActionResultSummary] = field(default_factory=list)

    def add_action_result(self, result: ActionResultSummary) -> None:
        self.last_action_result = result
        self.action_history.append(result)
        self.action_history = self.action_history[-10:]
        if result.action_kind:
            self.repeated_action_kinds[result.action_kind] = self.repeated_action_kinds.get(result.action_kind, 0) + 1
        for failure in result.failures:
            self.repeated_failure_signatures[failure.signature] = (
                self.repeated_failure_signatures.get(failure.signature, 0) + 1
            )

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


def load_beliefs(repo: Path, objective: str) -> WorkspaceBeliefState | None:
    target = repo / BELIEF_PATH
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("objective", "")).strip() and str(raw.get("objective", "")).strip() != objective.strip():
        return None
    try:
        return WorkspaceBeliefState(
            objective=objective,
            workspace_summary=str(raw.get("workspace_summary", "")),
            artifact_inventory=[str(v) for v in raw.get("artifact_inventory", []) if isinstance(v, str)],
            likely_deliverables=[str(v) for v in raw.get("likely_deliverables", []) if isinstance(v, str)],
            runnable_entrypoints=[str(v) for v in raw.get("runnable_entrypoints", []) if isinstance(v, str)],
            test_inventory=[str(v) for v in raw.get("test_inventory", []) if isinstance(v, str)],
            validation_observations=[
                ValidationObservation(
                    command=str(v.get("command", "")),
                    exit_code=int(v.get("exit_code", 1)),
                    source=str(v.get("source", "command")),
                )
                for v in raw.get("validation_observations", [])
                if isinstance(v, dict)
            ],
            known_failures=[
                FailureObservation(
                    signature=str(v.get("signature", "")),
                    detail=str(v.get("detail", "")),
                    source=str(v.get("source", "unknown")),
                    is_critical=bool(v.get("is_critical", True)),
                )
                for v in raw.get("known_failures", [])
                if isinstance(v, dict)
            ],
            scratch_artifacts=[str(v) for v in raw.get("scratch_artifacts", []) if isinstance(v, str)],
            validated_artifacts=[str(v) for v in raw.get("validated_artifacts", []) if isinstance(v, str)],
            recent_meaningful_changes=[str(v) for v in raw.get("recent_meaningful_changes", []) if isinstance(v, str)],
            repeated_patterns=[str(v) for v in raw.get("repeated_patterns", []) if isinstance(v, str)],
            repeated_action_kinds={
                str(k): int(v) for k, v in raw.get("repeated_action_kinds", {}).items() if isinstance(k, str)
            },
            repeated_failure_signatures={
                str(k): int(v)
                for k, v in raw.get("repeated_failure_signatures", {}).items()
                if isinstance(k, str)
            },
            completion_confidence=float(raw.get("completion_confidence", 0.0)),
            materially_satisfied=bool(raw.get("materially_satisfied", False)),
            unresolved_critical_issues=[
                str(v) for v in raw.get("unresolved_critical_issues", []) if isinstance(v, str)
            ],
            action_history=[],
        )
    except Exception:
        return None
