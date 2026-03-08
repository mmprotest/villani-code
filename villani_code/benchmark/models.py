from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BENCHMARK_VERSION = "2.0.0"


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


class FailureReason(str, Enum):
    VISIBLE_VERIFICATION_FAILED = "visible_verification_failed"
    HIDDEN_VERIFICATION_FAILED = "hidden_verification_failed"
    TIMEOUT = "timeout"
    FORBIDDEN_EDIT = "forbidden_edit"
    MISSING_ARTIFACT = "missing_artifact"
    AGENT_CRASH = "agent_crash"
    VERIFIER_CRASH = "verifier_crash"
    INVALID_REPRO_TEST = "invalid_repro_test"
    BENCHMARK_ERROR = "benchmark_error"


class TaskSource(str, Enum):
    SEEDED = "seeded"
    CURATED = "curated"
    MUTATED = "mutated"


class SuccessPolicy(BaseModel):
    require_visible_pass: bool = True
    require_hidden_pass: bool = True
    fail_on_timeout: bool = True
    fail_on_repo_dirty_outside_allowlist: bool = True


class BenchmarkTask(BaseModel):
    id: str
    family: TaskFamily
    difficulty: TaskDifficulty
    language: str
    max_minutes: int = Field(ge=1)
    max_files_touched: int = Field(ge=1)
    expected_artifacts: list[str]
    visible_verification: list[str]
    hidden_verification: list[str]
    success_policy: SuccessPolicy
    allowlist_paths: list[str]
    source_type: TaskSource = TaskSource.CURATED
    tags: list[str] = Field(default_factory=list)
    task_version: str = "1.0"
    primary_skill: str | None = None
    expected_files: list[str] = Field(default_factory=list)

    task_dir: Path
    prompt: str
    metadata: dict[str, object] = Field(default_factory=dict)
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
        if not (self.task_dir / "repo").is_dir():
            raise ValueError(f"Task {self.id} is missing repo/")
        return self


class VerificationOutcome(BaseModel):
    command: str
    passed: bool
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: float
    finished_at: float


class ReproManifest(BaseModel):
    broken_pass: bool
    fixed_pass: bool
    candidate_valid: bool
    reason: str | None = None


class ReproducibilityManifest(BaseModel):
    benchmark_version: str
    task_id: str
    task_version: str
    task_checksum: str
    platform: str
    python_version: str
    timeout_seconds: int
    agent_name: str
    model_name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    adapter_version: str = "1"


class BenchmarkRunResult(BaseModel):
    benchmark_version: str = BENCHMARK_VERSION
    task_id: str
    task_family: TaskFamily
    task_difficulty: TaskDifficulty
    task_language: str
    task_checksum: str
    agent_name: str
    agent_adapter_version: str = "1"
    model_name: str | None
    provider_label: str | None = None
    success: int
    visible_pass: bool
    hidden_pass: bool
    runtime_seconds: float
    timeout: bool
    failure_reason: FailureReason | None = None
    error: str | None = None
    touched_file_paths: list[str]
    files_touched: int
    lines_added: int
    lines_deleted: int
    num_shell_commands: int | None = None
    num_failed_commands: int | None = None
    verifications_run: list[str]
    time_to_first_edit: float | None = None
    time_to_first_verify: float | None = None
    last_verification_time: float | None = None
    telemetry_quality: TelemetryQuality = TelemetryQuality.UNAVAILABLE
    workspace_preserved: bool = False
    reproducibility_manifest_path: str | None = None
    inferred_fields: list[str] = Field(default_factory=list)


class BenchmarkSummary(BaseModel):
    total_tasks: int
    successes: int
    success_rate: float
    by_family: dict[str, dict[str, float]]
