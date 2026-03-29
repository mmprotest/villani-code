from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MissionType(StrEnum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    GREENFIELD_BUILD = "greenfield_build"
    REGRESSION_CONTAINMENT = "regression_containment"
    REPO_STABILIZATION = "repo_stabilization"
    VALIDATION_ONLY = "validation_only"
    NARROW_REFACTOR = "narrow_refactor"
    MAINTENANCE = "maintenance"


class NodePhase(StrEnum):
    INSPECT_WORKSPACE = "inspect_workspace"
    CHOOSE_PROJECT_DIRECTION = "choose_project_direction"
    SCAFFOLD_PROJECT = "scaffold_project"
    IMPLEMENT_VERTICAL_SLICE = "implement_vertical_slice"
    VALIDATE_PROJECT = "validate_project"
    SUMMARIZE_OUTCOME = "summarize_outcome"
    LOCALIZE = "localize"
    INSPECT = "inspect"
    REPRODUCE = "reproduce"
    NARROW_FIX = "narrow_fix"
    BROAD_FIX = "broad_fix"
    VALIDATE = "validate"
    RECOVER = "recover"
    SUMMARIZE = "summarize"


class NodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    SKIPPED = "skipped"


class MissionOutcome(StrEnum):
    SOLVED = "solved"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"
    STAGNATED = "stagnated"
    UNSAFE = "unsafe"
    BUDGET_EXHAUSTED = "budget_exhausted"


class DeltaClassification(StrEnum):
    STRONG_IMPROVEMENT = "strong_improvement"
    WEAK_IMPROVEMENT = "weak_improvement"
    NO_IMPROVEMENT = "no_improvement"
    REGRESSION = "regression"
    AMBIGUOUS = "ambiguous"


@dataclass(slots=True)
class LocalizationSnapshot:
    target_files: list[str] = field(default_factory=list)
    likely_bug_class: str = "unknown"
    repair_intent: str = ""
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    suggested_validation_commands: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NodeOutcomeRecord:
    status: str = "unknown"
    delta_classification: str = DeltaClassification.AMBIGUOUS.value
    delta_reason: str = ""
    changed_files: list[str] = field(default_factory=list)
    patch_detected: bool = False
    meaningful_patch: bool = False
    validation_summary: dict[str, Any] = field(default_factory=dict)
    failure_fingerprint: str = ""
    localization_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MissionNode:
    node_id: str
    title: str
    phase: NodePhase
    objective: str
    contract_type: str
    priority: float = 0.5
    confidence: float = 0.5
    candidate_files: list[str] = field(default_factory=list)
    validation_commands: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    attempts: int = 0
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    created_from_node_id: str = ""
    failure_fingerprint: str = ""
    localization: LocalizationSnapshot = field(default_factory=LocalizationSnapshot)
    last_outcome: NodeOutcomeRecord = field(default_factory=NodeOutcomeRecord)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionNode":
        return cls(
            node_id=str(data.get("node_id", "")),
            title=str(data.get("title", "")),
            phase=NodePhase(str(data.get("phase", NodePhase.INSPECT.value))),
            objective=str(data.get("objective", "")),
            contract_type=str(data.get("contract_type", "inspect")),
            priority=float(data.get("priority", 0.5)),
            confidence=float(data.get("confidence", 0.5)),
            candidate_files=list(data.get("candidate_files", []) or []),
            validation_commands=list(data.get("validation_commands", []) or []),
            depends_on=list(data.get("depends_on", []) or []),
            status=NodeStatus(str(data.get("status", NodeStatus.PENDING.value))),
            attempts=int(data.get("attempts", 0)),
            evidence=[str(x) for x in (data.get("evidence", []) or [])],
            blockers=[str(x) for x in (data.get("blockers", []) or [])],
            created_from_node_id=str(data.get("created_from_node_id", "")),
            failure_fingerprint=str(data.get("failure_fingerprint", "")),
            localization=LocalizationSnapshot(**dict(data.get("localization", {}) or {})),
            last_outcome=NodeOutcomeRecord(**dict(data.get("last_outcome", {}) or {})),
        )


@dataclass(slots=True)
class Mission:
    mission_id: str
    user_goal: str
    mission_type: MissionType
    success_criteria: list[str]
    repo_root: str
    state: str = "running"
    nodes: list[MissionNode] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    final_outcome: str = ""
    stop_reason: str = ""
    mission_context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "user_goal": self.user_goal,
            "mission_type": self.mission_type.value,
            "success_criteria": list(self.success_criteria),
            "repo_root": self.repo_root,
            "state": self.state,
            "nodes": [n.to_dict() for n in self.nodes],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "final_outcome": self.final_outcome,
            "stop_reason": self.stop_reason,
            "mission_context": dict(self.mission_context),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mission":
        return cls(
            mission_id=str(data.get("mission_id", "")),
            user_goal=str(data.get("user_goal", "")),
            mission_type=MissionType(str(data.get("mission_type", MissionType.MAINTENANCE.value))),
            success_criteria=[str(x) for x in (data.get("success_criteria", []) or [])],
            repo_root=str(data.get("repo_root", ".")),
            state=str(data.get("state", "running")),
            nodes=[MissionNode.from_dict(x) for x in (data.get("nodes", []) or [])],
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            final_outcome=str(data.get("final_outcome", "")),
            stop_reason=str(data.get("stop_reason", "")),
            mission_context=dict(data.get("mission_context", {}) or {}),
        )


@dataclass(slots=True)
class MissionExecutionState:
    mission: Mission
    active_node_id: str = ""
    inspected_files: list[str] = field(default_factory=list)
    attempted_actions: list[str] = field(default_factory=list)
    failed_strategies: list[str] = field(default_factory=list)
    evidence_log: list[str] = field(default_factory=list)
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    changed_files_baseline: list[str] = field(default_factory=list)
    consecutive_no_progress: int = 0
    consecutive_no_model_activity: int = 0
    last_localization: LocalizationSnapshot = field(default_factory=LocalizationSnapshot)
    localization_history: list[LocalizationSnapshot] = field(default_factory=list)
    failure_fingerprint_history: list[str] = field(default_factory=list)
    latest_validation_summary: dict[str, Any] = field(default_factory=dict)
    latest_changed_files: list[str] = field(default_factory=list)
    latest_execution_payload: dict[str, Any] = field(default_factory=dict)
    latest_command_results: list[dict[str, Any]] = field(default_factory=list)
    repeated_delta_states: int = 0
    greenfield_candidates: list[dict[str, Any]] = field(default_factory=list)
    greenfield_selection: dict[str, Any] = field(default_factory=dict)
    greenfield_progress: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission": self.mission.to_dict(),
            "active_node_id": self.active_node_id,
            "inspected_files": list(self.inspected_files),
            "attempted_actions": list(self.attempted_actions),
            "failed_strategies": list(self.failed_strategies),
            "evidence_log": list(self.evidence_log),
            "verification_history": list(self.verification_history),
            "changed_files_baseline": list(self.changed_files_baseline),
            "consecutive_no_progress": self.consecutive_no_progress,
            "consecutive_no_model_activity": self.consecutive_no_model_activity,
            "last_localization": asdict(self.last_localization),
            "localization_history": [asdict(x) for x in self.localization_history],
            "failure_fingerprint_history": list(self.failure_fingerprint_history),
            "latest_validation_summary": dict(self.latest_validation_summary),
            "latest_changed_files": list(self.latest_changed_files),
            "latest_execution_payload": dict(self.latest_execution_payload),
            "latest_command_results": list(self.latest_command_results),
            "repeated_delta_states": self.repeated_delta_states,
            "greenfield_candidates": list(self.greenfield_candidates),
            "greenfield_selection": dict(self.greenfield_selection),
            "greenfield_progress": dict(self.greenfield_progress),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MissionExecutionState":
        return cls(
            mission=Mission.from_dict(dict(data.get("mission", {}) or {})),
            active_node_id=str(data.get("active_node_id", "")),
            inspected_files=[str(x) for x in (data.get("inspected_files", []) or [])],
            attempted_actions=[str(x) for x in (data.get("attempted_actions", []) or [])],
            failed_strategies=[str(x) for x in (data.get("failed_strategies", []) or [])],
            evidence_log=[str(x) for x in (data.get("evidence_log", []) or [])],
            verification_history=list(data.get("verification_history", []) or []),
            changed_files_baseline=[str(x) for x in (data.get("changed_files_baseline", []) or [])],
            consecutive_no_progress=int(data.get("consecutive_no_progress", 0)),
            consecutive_no_model_activity=int(data.get("consecutive_no_model_activity", 0)),
            last_localization=LocalizationSnapshot(**dict(data.get("last_localization", {}) or {})),
            localization_history=[LocalizationSnapshot(**dict(x or {})) for x in (data.get("localization_history", []) or [])],
            failure_fingerprint_history=[str(x) for x in (data.get("failure_fingerprint_history", []) or [])],
            latest_validation_summary=dict(data.get("latest_validation_summary", {}) or {}),
            latest_changed_files=[str(x) for x in (data.get("latest_changed_files", []) or [])],
            latest_execution_payload=dict(data.get("latest_execution_payload", {}) or {}),
            latest_command_results=list(data.get("latest_command_results", []) or []),
            repeated_delta_states=int(data.get("repeated_delta_states", 0)),
            greenfield_candidates=list(data.get("greenfield_candidates", []) or []),
            greenfield_selection=dict(data.get("greenfield_selection", {}) or {}),
            greenfield_progress=dict(data.get("greenfield_progress", {}) or {}),
        )
