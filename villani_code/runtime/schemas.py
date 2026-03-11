from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, Field


class StopReason(StrEnum):
    SOLVED = "solved"
    EXHAUSTED_BUDGET = "exhausted_budget"
    NO_PROGRESS = "no_progress"
    BLOCKED = "blocked"
    SCOPE_EXPANSION_REQUIRED = "scope_expansion_required"
    VERIFIER_UNAVAILABLE = "verifier_unavailable"
    TIMEOUT_IMMINENT = "timeout_imminent"


class HypothesisClass(StrEnum):
    BOUNDARY_ERROR = "boundary_error"
    STALE_STATE = "stale_state"
    CONTRACT_MISMATCH = "contract_mismatch"
    MISSING_PROPAGATION = "missing_propagation"
    WRONG_ERROR_PATH = "wrong_error_path"
    DEPENDENCY_MISUSE = "dependency_misuse"
    NULL_OR_EMPTY_CASE = "null_or_empty_case"
    OFF_BY_ONE = "off_by_one"
    TYPE_ASSUMPTION = "type_assumption"
    PATH_OR_IMPORT_ERROR = "path_or_import_error"


class RuntimeBudgets(BaseModel):
    max_cycles: int = 8
    max_active_branches: int = 6
    max_total_branches: int = 24
    max_hypotheses_per_suspect: int = 5
    max_candidates_per_hypothesis: int = 2
    max_total_verifier_calls: int = 80
    max_patch_lines: int = 20
    max_files_per_patch: int = 1
    max_scope_expansions: int = 2
    max_consecutive_no_improvement_cycles: int = 2


class Evidence(BaseModel):
    failing_tests: list[str] = Field(default_factory=list)
    stack_traces: list[str] = Field(default_factory=list)
    error_messages: list[str] = Field(default_factory=list)
    repro_commands: list[str] = Field(default_factory=list)
    visible_verification_commands: list[str] = Field(default_factory=list)
    hidden_verification_commands: list[str] = Field(default_factory=list)
    benchmark_expected_files: list[str] = Field(default_factory=list)
    benchmark_allowlist_paths: list[str] = Field(default_factory=list)


class SuspectRegion(BaseModel):
    file: str
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    score: float = 0.0
    signal_breakdown: dict[str, float] = Field(default_factory=dict)


class HypothesisRecord(BaseModel):
    id: str
    suspect_ref: str
    text: str
    hypothesis_class: HypothesisClass
    plausibility_score: float
    diversity_bucket: str
    status: str = "proposed"
    notes: str = ""


class BranchRecord(BaseModel):
    id: str
    suspect_ref: str
    hypothesis_id: str
    status: str = "active"
    best_score: float = 0.0
    repeated_failure_signatures: list[str] = Field(default_factory=list)
    scope_level: str = "symbol"
    attempts_list: list[str] = Field(default_factory=list)


class AttemptRecord(BaseModel):
    id: str
    branch_id: str
    files_touched: list[str] = Field(default_factory=list)
    symbols_touched: list[str] = Field(default_factory=list)
    changed_line_count: int = 0
    verifier_outputs: dict[str, object] = Field(default_factory=dict)
    score: float = 0.0
    hard_fail: bool = False
    result: str = "failed"
    reason: str = ""


class Blackboard(BaseModel):
    run_id: str
    task_id: str
    objective: str
    repo_root: str
    cycle: int = 0
    budgets: RuntimeBudgets
    evidence: Evidence = Field(default_factory=Evidence)
    constraints: dict[str, object] = Field(default_factory=dict)
    suspects: list[SuspectRegion] = Field(default_factory=list)
    hypotheses: list[HypothesisRecord] = Field(default_factory=list)
    branches: list[BranchRecord] = Field(default_factory=list)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    decision_log: list[dict[str, object]] = Field(default_factory=list)
    final_result: dict[str, object] = Field(default_factory=dict)
