from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


WorkerStatus = Literal["success", "blocked_environment", "blocked_scope", "failed"]


@dataclass(slots=True)
class Subtask:
    id: str
    goal: str
    success_criteria: list[str] = field(default_factory=list)
    target_files: list[str] = field(default_factory=list)
    scope_hint: str = ""


@dataclass(slots=True)
class SupervisorResult:
    subtasks: list[Subtask]


@dataclass(slots=True)
class WorkerResult:
    status: WorkerStatus
    summary: str
    files_touched: list[str] = field(default_factory=list)
    recommended_verification: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VerificationOutcome:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
