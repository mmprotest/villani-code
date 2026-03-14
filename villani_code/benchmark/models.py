from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

BENCHMARK_VERSION = "3.0.0"


class BenchmarkTrack(str, Enum):
    CORE = "core"
    FEATURE = "feature"


class TaskFamily(str, Enum):
    BUGFIX = "bugfix"
    REPRO_TEST = "repro_test"
    LOCALIZE_PATCH = "localize_patch"
    TERMINAL_WORKFLOW = "terminal_workflow"


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TelemetryQuality(str, Enum):
    EXACT = "exact"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


class FieldQuality(str, Enum):
    EXACT = "exact"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


class FailureReason(str, Enum):
    VISIBLE_VERIFICATION_FAILED = "visible_verification_failed"
    HIDDEN_VERIFICATION_FAILED = "hidden_verification_failed"
    TIMEOUT = "timeout"
    FORBIDDEN_EDIT = "forbidden_edit"
    INSPECT_ONLY_VIOLATION = "inspect_only_violation"
    MISSING_ARTIFACT = "missing_artifact"
    AGENT_CRASH = "agent_crash"
    VERIFIER_CRASH = "verifier_crash"
    VERIFICATION_COMMAND_FAILED_TO_LAUNCH = "verification_command_failed_to_launch"
    INVALID_REPRO_TEST = "invalid_repro_test"
    BENCHMARK_ERROR = "benchmark_error"
    NO_PROGRESS = "no_progress"
    BENCHMARK_NO_PATCH_ATTEMPT = "benchmark_no_patch_attempt"
    ENVIRONMENT_FAILURE = "environment_failure"


class TaskSource(str, Enum):
    SEEDED = "seeded"
    CURATED = "curated"
    MUTATED = "mutated"
    HELD_OUT = "held_out"
    SYNTHETIC_EXECUTION_GROUNDED = "synthetic_execution_grounded"


class FairnessClassification(str, Enum):
    EXACT_COMPARABLE = "exact_comparable"
    APPROXIMATELY_COMPARABLE = "approximately_comparable"
    COARSE_WRAPPER_ONLY = "coarse_wrapper_only"
    NOT_COMPARABLE = "not_comparable"


class SuccessPolicy(BaseModel):
    require_visible_pass: bool = True
    require_hidden_pass: bool = True
    fail_on_timeout: bool = True
    fail_on_repo_dirty_outside_allowlist: bool = True


class TaskMetadata(BaseModel):
    name: str | None = None
    difficulty: str | None = None
    expected_files: list[str] = Field(default_factory=list)
    allowed_support_files: list[str] = Field(default_factory=list)
    allowed_support_globs: list[str] = Field(default_factory=list)
    reference_patch_size_lines: int | None = None
    primary_skill: str | None = None
    secondary_skills: list[str] = Field(default_factory=list)
    runtime_stressors: list[str] = Field(default_factory=list)
    task_type: str | None = None
    requires_repo_navigation: bool = False
    requires_multi_step_reasoning: bool = False
    has_false_fix_trap: bool = False
    requires_retry_recovery: bool = False
    likely_tool_sequence: list[str] = Field(default_factory=list)
    evaluation_focus: list[str] = Field(default_factory=list)
    benchmark_bucket: str = "baseline"
    failure_mode: str | None = None
    reference_solution_notes: str | None = None
    mutability_notes: str | None = None



    @field_validator("benchmark_bucket")
    @classmethod
    def _validate_bucket(cls, value: str) -> str:
        if value not in {"baseline", "runtime_stressing"}:
            raise ValueError("benchmark_bucket must be baseline|runtime_stressing")
        return value

class BenchmarkTask(BaseModel):
    id: str
    benchmark_track: BenchmarkTrack
    family: TaskFamily
    difficulty: TaskDifficulty
    language: str
    task_version: str = "1.0"
    source_type: TaskSource = TaskSource.CURATED
    tags: list[str] = Field(default_factory=list)
    max_minutes: int = Field(ge=1)
    max_files_touched: int = Field(ge=1)
    expected_patch_size_band: str = "small"
    expected_artifacts: list[str] = Field(default_factory=lambda: ["patch"])
    visible_verification: list[str]
    hidden_verification: list[str]
    hidden_verifier: list[str] | None = None
    success_policy: SuccessPolicy
    allowlist_paths: list[str]
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    expected_touched_max: int | None = None
    inspect_only: bool = False
    recovery_expected: bool = False
    adjacency_expected: bool = False
    task_type: str | None = None
    env_allowlist: list[str] = Field(default_factory=list)
    task_variant_family: str | None = None
    variant_id: str | None = None

    task_dir: Path
    prompt: str
    metadata: TaskMetadata = Field(default_factory=TaskMetadata)
    task_checksum: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not value or " " in value:
            raise ValueError("Task id must be non-empty and contain no spaces")
        return value

    @field_validator("allowlist_paths")
    @classmethod
    def _validate_allowlist(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("allowlist_paths must not be empty")
        return values

    @model_validator(mode="after")
    def _validate_task_dir(self) -> "BenchmarkTask":
        if self.hidden_verifier:
            self.hidden_verifier = None
        if not (self.task_dir / "repo").is_dir():
            raise ValueError(f"Task {self.id} is missing repo/")
        return self


class VerificationOutcome(BaseModel):
    command: str
    normalized_command: str | None = None
    passed: bool
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: float
    finished_at: float
    stdout_artifact: str | None = None
    stderr_artifact: str | None = None
    metadata_artifact: str | None = None


class ReproducibilityManifest(BaseModel):
    benchmark_version: str
    task_id: str
    task_version: str
    task_checksum: str
    repo_checksum: str
    visible_check_checksum: str
    hidden_check_checksum: str
    adapter_name: str
    adapter_version: str
    timeout_seconds: int
    repeat_index: int = 0
    platform: str
    python_version: str
    agent_name: str
    model_name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    env_allowlist: list[str] = Field(default_factory=list)
    telemetry_quality: TelemetryQuality = TelemetryQuality.UNAVAILABLE
    workspace_preserved: bool = False


class BenchmarkRunResult(BaseModel):
    benchmark_version: str = BENCHMARK_VERSION
    benchmark_track: BenchmarkTrack = BenchmarkTrack.CORE
    task_id: str
    task_version: str = "1.0"
    task_family: TaskFamily
    task_difficulty: TaskDifficulty
    task_language: str
    task_source_type: TaskSource = TaskSource.CURATED
    task_tags: list[str] = Field(default_factory=list)
    task_type: str | None = None
    benchmark_bucket: str = "baseline"
    runtime_stressors: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    task_checksum: str
    agent_name: str
    adapter_name: str
    adapter_version: str
    adapter_capability: str
    fairness_classification: FairnessClassification
    fairness_notes: str
    telemetry_capability: str
    model_name: str | None
    provider_label: str | None = None
    success: int
    pass_rate: float = 0.0
    failed: int = 0
    timed_out: int = 0
    visible_pass: bool
    hidden_pass: bool
    visible_only_pass: bool = False
    runtime_seconds: float
    wall_clock_seconds: float | None = None
    timeout: bool
    failure_reason: FailureReason | None = None
    forbidden_reason_detail: str | None = None
    policy_warning: str | None = None
    policy_warning_detail: str | None = None
    error: str | None = None
    agent_exit_code: int | None = None
    stderr_preview: str | None = None
    touched_file_paths: list[str]
    raw_touched_file_paths: list[str] = Field(default_factory=list)
    normalized_touched_paths: list[str] = Field(default_factory=list)
    path_classifications: dict[str, str] = Field(default_factory=dict)
    meaningful_touched_paths: list[str] = Field(default_factory=list)
    meaningful_expected_paths: list[str] = Field(default_factory=list)
    meaningful_unexpected_paths: list[str] = Field(default_factory=list)
    files_touched: int
    lines_added: int
    lines_deleted: int
    num_shell_commands: int | None = None
    num_failed_commands: int | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    number_of_turns: int | None = None
    tool_calls_total: int | None = None
    file_reads: int | None = None
    file_writes: int | None = None
    patch_attempts: int | None = None
    test_runs: int | None = None
    retries_after_failure: int | None = None
    first_pass_success: bool | None = None
    recovered_after_failed_attempt: bool | None = None
    expected_files_touched_count: int | None = None
    actual_files_touched_count: int | None = None
    touched_unexpected_files: bool | None = None
    unrelated_file_touch: bool = False
    verification_relevant: bool = False
    recovery_attempted: bool = False
    recovery_success: bool | None = None
    no_progress_termination: bool = False
    verifications_run: list[str]
    verification_attempt_count: int = 0
    time_to_first_edit: float | None = None
    time_to_first_verify: float | None = None
    last_verification_time: float | None = None
    expected_files_found: int | None = None
    expected_files_total: int | None = None
    expected_file_first_read_time: float | None = None
    self_corrected_after_failed_verify: bool | None = None
    touched_irrelevant_files: int | None = None
    telemetry_quality: TelemetryQuality = TelemetryQuality.UNAVAILABLE
    telemetry_field_quality_map: dict[str, FieldQuality] = Field(default_factory=dict)
    workspace_preserved: bool = False
    reproducibility_manifest_path: str | None = None
    prompt_artifact_path: str | None = None
    contract_artifact_path: str | None = None
    scoring_inputs_mode: str = "harness_only"
    repeat_index: int = 0


class BenchmarkSummary(BaseModel):
    total_tasks: int
    successes: int
    success_rate: float
    by_family: dict[str, dict[str, float]]
