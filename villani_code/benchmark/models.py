from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ValidationCheckType(str, Enum):
    COMMAND = "command"
    FILE_CONTAINS = "file_contains"
    FILE_NOT_CONTAINS = "file_not_contains"


class ValidationCheck(BaseModel):
    type: ValidationCheckType
    command: str | None = None
    cwd: str | None = None
    expect_exit_code: int = 0
    path: str | None = None
    substring: str | None = None

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "ValidationCheck":
        if self.type == ValidationCheckType.COMMAND and not self.command:
            raise ValueError("command check requires command")
        if self.type in {ValidationCheckType.FILE_CONTAINS, ValidationCheckType.FILE_NOT_CONTAINS}:
            if not self.path:
                raise ValueError(f"{self.type.value} check requires path")
            if self.substring is None:
                raise ValueError(f"{self.type.value} check requires substring")
        return self


class BenchmarkTask(BaseModel):
    id: str
    name: str
    repo_path: str | None = None
    repo_git_ref: str | None = None
    instruction: str
    category: str
    tags: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300
    max_turns: int | None = None
    expected_touched_paths: list[str] = Field(default_factory=list)
    forbidden_touched_paths: list[str] = Field(default_factory=list)
    validation_checks: list[ValidationCheck] = Field(default_factory=list)
    success_threshold: float | None = None
    notes: str | None = None


class BenchmarkMetadata(BaseModel):
    benchmark_name: str = "villani-agent-benchmark"
    model: str | None = None
    base_url: str | None = None
    seed: int | None = None
    repo_snapshot: str | None = None


class ValidationResult(BaseModel):
    check_type: ValidationCheckType
    success: bool
    details: str
    exit_code: int | None = None
    check_index: int = 0


@dataclass(slots=True)
class Scorecard:
    task_success: bool
    validation_success: bool
    elapsed_seconds: float
    changed_files_count: int
    unnecessary_files_touched_count: int
    forbidden_files_touched_count: int
    catastrophic_failure: bool
    retry_count: int = 0
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    skipped: bool = False
    composite_score: float = 0.0


@dataclass(slots=True)
class TaskExecutionResult:
    agent_name: str
    task_id: str
    success: bool
    exit_reason: str
    elapsed_seconds: float
    stdout: str
    stderr: str
    changed_files: list[str]
    git_diff: str
    validation_results: list[ValidationResult] = field(default_factory=list)
    catastrophic_failure: bool = False
    tokens_input: int | None = None
    tokens_output: int | None = None
    cost_usd: float | None = None
    raw_artifact_dir: str = ""
    skipped: bool = False
    skip_reason: str | None = None
    retry_count: int = 0


class AgentSelection(BaseModel):
    name: Literal["villani", "claude-code", "opencode", "copilot-cli"]


def ensure_within_repo(path: str, repo_root: Path) -> Path:
    resolved = (repo_root / path).resolve()
    if not str(resolved).startswith(str(repo_root.resolve())):
        raise ValueError(f"Path escapes repository root: {path}")
    return resolved


def as_serializable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump()
    if isinstance(data, Path):
        return str(data)
    if isinstance(data, list):
        return [as_serializable(item) for item in data]
    if isinstance(data, dict):
        return {str(k): as_serializable(v) for k, v in data.items()}
    if hasattr(data, "__dict__"):
        return {k: as_serializable(v) for k, v in data.__dict__.items()}
    return data
