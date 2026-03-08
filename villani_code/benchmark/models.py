from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TaskFamily(str, Enum):
    BUGFIX = "bugfix"
    REPRO_TEST = "repro_test"
    LOCALIZE_PATCH = "localize_patch"
    TERMINAL_WORKFLOW = "terminal_workflow"


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


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

    task_dir: Path
    prompt: str
    metadata: dict[str, object] = Field(default_factory=dict)

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


class RunStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


class VerificationOutcome(BaseModel):
    command: str
    passed: bool
    exit_code: int | None
    stdout: str
    stderr: str


class BenchmarkRunResult(BaseModel):
    task_id: str
    agent: str
    model: str | None
    family: TaskFamily
    difficulty: TaskDifficulty
    success: int
    visible_pass: bool
    hidden_pass: bool
    runtime_seconds: float
    files_touched: int
    touched_file_paths: list[str]
    lines_added: int
    lines_deleted: int
    num_shell_commands: int
    num_failed_commands: int
    verifications_run: list[str]
    timeout: bool
    error: str | None = None
    time_to_first_edit: float | None = None
    time_to_first_verify: float | None = None
    status: RunStatus = RunStatus.SUCCESS


class BenchmarkSummary(BaseModel):
    total_tasks: int
    successes: int
    success_rate: float
    by_family: dict[str, dict[str, float]]
