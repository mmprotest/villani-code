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




class PathAuthority(StrEnum):
    USER_WORKSPACE_AUTHORITATIVE = "user_workspace_authoritative"
    USER_WORKSPACE_SUPPORTING = "user_workspace_supporting"
    INTERNAL_ARTIFACT_LOW_AUTHORITY = "internal_artifact_low_authority"
    INTERNAL_ARTIFACT_IGNORED = "internal_artifact_ignored"


@dataclass(slots=True)
class MissionScratchpad:
    mission_goal: str = ""
    mission_type: str = MissionType.MAINTENANCE.value
    current_phase: str = ""
    chosen_project_direction: str = ""
    selection_rationale: str = ""
    hard_constraints: list[str] = field(default_factory=list)
    allowed_output_locations: list[str] = field(default_factory=list)
    ignored_internal_paths: list[str] = field(default_factory=list)
    confirmed_deliverables: list[str] = field(default_factory=list)
    files_created_so_far: list[str] = field(default_factory=list)
    last_successful_action: str = ""
    current_blockers: list[str] = field(default_factory=list)
    next_required_action: str = ""
    ruled_out_directions: list[str] = field(default_factory=list)
    validation_intent: str = ""
    validation_commands: list[str] = field(default_factory=list)
    no_confirmation_required: bool = True
    no_internal_artifact_deliverables: bool = True
    workspace_classification: str = "unknown"
    chosen_product_shape: str = ""
    minimal_vertical_slice_target: str = ""
    has_user_space_scaffolding: bool = False
    has_runnable_entrypoint: bool = False
    validation_proven: bool = False
    path_authority: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def update_from_greenfield_selection(self, selection: dict[str, Any], candidates: list[dict[str, Any]] | None = None) -> None:
        chosen = str(selection.get("project_type", "")).strip()
        if chosen and not self.chosen_project_direction:
            self.chosen_project_direction = chosen
            self.chosen_product_shape = chosen
            self.selection_rationale = str(selection.get("selection_rationale", "")).strip()
        elif chosen and self.chosen_project_direction and chosen != self.chosen_project_direction:
            pool = list(candidates or [])
            chosen_score = 0.0
            current_score = 0.0
            for c in pool:
                if str(c.get("project_type", "")) == chosen:
                    chosen_score = float(c.get("score", 0.0) or 0.0)
                if str(c.get("project_type", "")) == self.chosen_project_direction:
                    current_score = float(c.get("score", 0.0) or 0.0)
            if chosen_score >= (current_score + 0.2):
                self.ruled_out_directions.append(self.chosen_project_direction)
                self.chosen_project_direction = chosen
                self.chosen_product_shape = chosen
                self.selection_rationale = str(selection.get("selection_rationale", "")).strip()
            else:
                self.ruled_out_directions.append(chosen)
        self.ruled_out_directions = list(dict.fromkeys([x for x in self.ruled_out_directions if x]))[-20:]

    def update_from_execution_result(self, node_phase: str, outcome_status: str, changed_files: list[str], blockers: list[str] | None = None) -> None:
        if outcome_status == "passed":
            self.last_successful_action = node_phase
            self.current_phase = node_phase
        if blockers:
            self.current_blockers = list(dict.fromkeys(self.current_blockers + [str(x) for x in blockers if str(x).strip()]))[-10:]
        created = [str(p) for p in changed_files if str(p).strip()]
        self.files_created_so_far = list(dict.fromkeys(self.files_created_so_far + created))

    def update_from_verification(self, deliverables: list[str], validation_summary: dict[str, Any], next_action: str = "") -> None:
        merged = list(dict.fromkeys(self.confirmed_deliverables + [str(p) for p in deliverables if str(p).strip()]))
        self.confirmed_deliverables = merged
        self.has_user_space_scaffolding = self.has_user_space_scaffolding or bool(merged)
        self.validation_commands = list(dict.fromkeys(self.validation_commands + [str(c) for c in list(validation_summary.get("commands", []) or []) if str(c).strip()]))
        if int(validation_summary.get("commands_run", 0) or 0) > 0:
            self.validation_proven = True
        if next_action:
            self.next_required_action = next_action

    def derive_next_action(self) -> str:
        if self.mission_type == MissionType.GREENFIELD_BUILD.value:
            if not self.chosen_project_direction:
                return NodePhase.CHOOSE_PROJECT_DIRECTION.value
            if not self.has_user_space_scaffolding:
                return NodePhase.SCAFFOLD_PROJECT.value
            if not self.has_runnable_entrypoint:
                return NodePhase.IMPLEMENT_VERTICAL_SLICE.value
            if not self.validation_proven:
                return NodePhase.VALIDATE_PROJECT.value
            return NodePhase.SUMMARIZE_OUTCOME.value
        return self.next_required_action or self.current_phase or NodePhase.INSPECT.value

class NodePhase(StrEnum):
    INSPECT_WORKSPACE = "inspect_workspace"
    DEFINE_OBJECTIVE = "define_objective"
    CHOOSE_PROJECT_DIRECTION = "define_objective"
    SCAFFOLD_PROJECT = "scaffold_project"
    IMPLEMENT_INCREMENT = "implement_increment"
    IMPLEMENT_VERTICAL_SLICE = "implement_increment"
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
    PARTIAL_SUCCESS_BUILT_UNVALIDATED = "partial_success_built_unvalidated"
    PARTIAL_SUCCESS_BUILT_VALIDATION_FAILED = "partial_success_built_validation_failed"
    PARTIAL_SUCCESS_SCAFFOLD_ONLY = "partial_success_scaffold_only"
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
    phase_contract_status: str = "unknown"
    mission_progress_status: str = "no_progress"
    delta_classification: str = DeltaClassification.AMBIGUOUS.value
    delta_reason: str = ""
    changed_files: list[str] = field(default_factory=list)
    patch_detected: bool = False
    meaningful_patch: bool = False
    validation_summary: dict[str, Any] = field(default_factory=dict)
    failure_fingerprint: str = ""
    localization_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedNodeOutcome:
    node_id: str
    node_phase: str
    contract_status: str
    mission_progress_status: str
    successful_user_writes: list[str] = field(default_factory=list)
    blocked_write_attempts: list[str] = field(default_factory=list)
    internal_artifact_writes: list[str] = field(default_factory=list)
    actual_changed_files_count: int = 0
    deliverable_paths: list[str] = field(default_factory=list)
    command_results: list[dict[str, Any]] = field(default_factory=list)
    validation_truth_status: str = "unproven"
    validation_summary: dict[str, Any] = field(default_factory=dict)
    realized_artifact_direction: str = ""
    next_recommended_action: str = ""
    terminal_candidate_state: str = ""
    blocked_reason: str = ""


@dataclass(slots=True)
class NormalizedMissionProgress:
    node_outcomes: list[NormalizedNodeOutcome] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    deliverable_paths: list[str] = field(default_factory=list)
    blocked_write_attempts: list[str] = field(default_factory=list)
    internal_artifact_writes: list[str] = field(default_factory=list)
    validation_truth_status: str = "unproven"
    validation_summary: dict[str, Any] = field(default_factory=dict)
    realized_artifact_direction: str = ""
    mission_completion_state: str = "partial"
    terminal_state: str = "stagnated"
    next_recommended_action: str = ""
    blocked_reason: str = ""


def select_validation_relevant_commands(command_results: list[dict[str, Any]], *, node_phase: str = "") -> list[dict[str, Any]]:
    phase = str(node_phase or "").strip().lower()
    if phase in {"inspect_workspace", "define_objective", "summarize_outcome"}:
        return []
    return [dict(item) for item in command_results if isinstance(item, dict)]


def reduce_validation_truth(command_results: list[dict[str, Any]], *, node_phase: str = "") -> tuple[str, dict[str, Any]]:
    relevant = select_validation_relevant_commands(command_results, node_phase=node_phase)
    total = len(relevant)
    if total <= 0:
        return "unproven", {"commands_run": 0, "passed": 0, "failed": 0, "evidence": "none"}
    failed = sum(1 for item in relevant if int(item.get("exit", 1) or 1) != 0)
    passed = max(0, total - failed)
    if failed > 0:
        return "failed", {"commands_run": total, "passed": passed, "failed": failed, "evidence": "real_command_results"}
    return "proven", {"commands_run": total, "passed": passed, "failed": 0, "evidence": "real_command_results"}


def infer_realized_artifact_direction(deliverables: list[str], fallback: str = "") -> str:
    joined = " ".join(str(path).lower() for path in deliverables if str(path).strip())
    if not joined:
        return fallback
    if "wordle" in joined or ("word" in joined and "guess" in joined):
        return "wordle_clone_game_cli"
    if "snake" in joined:
        return "snake_cli_game"
    if "adventure" in joined:
        return "text_adventure_cli"
    if any(token in joined for token in ("cli", "command", "console")):
        return "python_cli_utility"
    return fallback


def reduce_terminal_state(
    *,
    has_deliverables: bool,
    has_meaningful_progress: bool,
    validation_truth_status: str,
    blocked_write_attempts: list[str],
    explicit_blocked_reason: str = "",
) -> tuple[str, str]:
    blocked_reason = explicit_blocked_reason.strip()
    if blocked_reason or (blocked_write_attempts and not has_deliverables):
        return "blocked", blocked_reason or "write operations were blocked before deliverables were produced"
    if has_deliverables:
        if validation_truth_status == "proven":
            return "success", ""
        if validation_truth_status in {"failed", "unproven"}:
            return "partial_success", ""
    if not has_meaningful_progress:
        return "stagnated", ""
    if has_meaningful_progress and not has_deliverables and validation_truth_status == "unproven":
        return "in_progress", ""
    if validation_truth_status == "failed":
        return "failed", ""
    return "stagnated", ""


def reduce_normalized_mission_progress(state: "MissionExecutionState") -> NormalizedMissionProgress:
    outcomes = list(state.normalized_node_outcomes or [])
    successful_user_writes = sorted(
        {
            str(path)
            for item in outcomes
            for path in list(item.successful_user_writes or [])
            if str(path).strip()
        }
    )
    deliverables = sorted(
        {
            str(path)
            for item in outcomes
            for path in list(item.deliverable_paths or [])
            if str(path).strip()
        }
    )
    blocked_writes = sorted(
        {
            str(path)
            for item in outcomes
            for path in list(item.blocked_write_attempts or [])
            if str(path).strip()
        }
    )
    internal_writes = sorted(
        {
            str(path)
            for item in outcomes
            for path in list(item.internal_artifact_writes or [])
            if str(path).strip()
        }
    )
    all_commands = [dict(cmd) for item in outcomes for cmd in list(item.command_results or []) if isinstance(cmd, dict)]
    phase_scoped_validation_commands = [
        dict(cmd)
        for item in outcomes
        for cmd in select_validation_relevant_commands(list(item.command_results or []), node_phase=str(item.node_phase))
    ]
    validation_truth_status, validation_summary = reduce_validation_truth(phase_scoped_validation_commands)
    fallback_direction = str(state.scratchpad.chosen_project_direction or state.greenfield_selection.get("project_type", "")).strip()
    realized_direction = infer_realized_artifact_direction(deliverables or successful_user_writes, fallback=fallback_direction)
    has_meaningful_progress = any(
        str(item.mission_progress_status) in {
            "validated_success",
            "validation_progress",
            "state_progress",
            "state_progress_partial",
            "artifact_progress",
            "summary_completed",
            "summary_partial",
            "useful_progress_unvalidated",
            "useful_progress_with_contract_violation",
        }
        for item in outcomes
    ) or bool(deliverables)
    blocked_reason = next((str(item.blocked_reason).strip() for item in reversed(outcomes) if str(item.blocked_reason).strip()), "")
    terminal_state, reduced_blocked_reason = reduce_terminal_state(
        has_deliverables=bool(deliverables),
        has_meaningful_progress=has_meaningful_progress,
        validation_truth_status=validation_truth_status,
        blocked_write_attempts=blocked_writes,
        explicit_blocked_reason=blocked_reason,
    )
    next_action = next((str(item.next_recommended_action).strip() for item in reversed(outcomes) if str(item.next_recommended_action).strip()), "")
    ready_greenfield_recovery = bool(
        state.mission.mission_type == MissionType.GREENFIELD_BUILD
        and any(node.status == NodeStatus.READY and str(node.created_from_node_id or "").strip() for node in state.mission.nodes)
    )
    if (
        state.mission.mission_type == MissionType.GREENFIELD_BUILD
        and terminal_state == "partial_success"
        and next_action in {NodePhase.SCAFFOLD_PROJECT.value, NodePhase.IMPLEMENT_INCREMENT.value, NodePhase.VALIDATE_PROJECT.value}
    ):
        terminal_state = "in_progress"
    has_read_only_forward_progress = bool(
        state.mission.mission_type == MissionType.GREENFIELD_BUILD
        and next_action
        and any(str(item.mission_progress_status) in {"state_progress", "state_progress_partial"} for item in outcomes)
    )
    if terminal_state == "stagnated" and (ready_greenfield_recovery and next_action or has_read_only_forward_progress):
        terminal_state = "in_progress"
    mission_completion_state = "complete" if terminal_state == "success" else ("partial" if terminal_state == "partial_success" else "incomplete")
    return NormalizedMissionProgress(
        node_outcomes=outcomes,
        files_touched=successful_user_writes,
        deliverable_paths=deliverables,
        blocked_write_attempts=blocked_writes,
        internal_artifact_writes=internal_writes,
        validation_truth_status=validation_truth_status,
        validation_summary=validation_summary,
        realized_artifact_direction=realized_direction,
        mission_completion_state=mission_completion_state,
        terminal_state=terminal_state,
        next_recommended_action="" if terminal_state in {"success", "failed", "blocked"} else next_action,
        blocked_reason=reduced_blocked_reason,
    )


class MissionExecutionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    CONTRACT_VIOLATION_RECOVERED = "contract_violation_recovered"
    CONTRACT_VIOLATION_UNRECOVERED = "contract_violation_unrecovered"


@dataclass(slots=True)
class MissionObjective:
    user_goal: str = ""
    repo_state_type: str = "unknown"
    task_shape: str = "mixed"
    deliverable_kind: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    success_signals: list[str] = field(default_factory=list)
    ambiguity_flags: list[str] = field(default_factory=list)
    initial_validation_strategy: list[str] = field(default_factory=list)
    direction: str = ""


@dataclass(slots=True)
class ProposedAction:
    phase: str
    action_type: str
    target_paths: list[str] = field(default_factory=list)
    rationale: str = ""
    expected_effect: str = ""
    risk_level: str = "low"


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
    objective: MissionObjective = field(default_factory=MissionObjective)

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
            "objective": asdict(self.objective),
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
            objective=MissionObjective(**dict(data.get("objective", {}) or {})),
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
    scratchpad: MissionScratchpad = field(default_factory=MissionScratchpad)
    normalized_node_outcomes: list[NormalizedNodeOutcome] = field(default_factory=list)
    recovery_state_note: str = ""
    recovery_nodes_inserted_last: int = 0

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
            "scratchpad": self.scratchpad.to_dict(),
            "normalized_node_outcomes": [asdict(item) for item in self.normalized_node_outcomes],
            "recovery_state_note": self.recovery_state_note,
            "recovery_nodes_inserted_last": self.recovery_nodes_inserted_last,
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
            scratchpad=MissionScratchpad(**dict(data.get("scratchpad", {}) or {})),
            normalized_node_outcomes=[
                NormalizedNodeOutcome(**dict(item or {}))
                for item in (data.get("normalized_node_outcomes", []) or [])
            ],
            recovery_state_note=str(data.get("recovery_state_note", "")),
            recovery_nodes_inserted_last=int(data.get("recovery_nodes_inserted_last", 0)),
        )
