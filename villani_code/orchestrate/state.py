from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PatchUnit:
    title: str
    objective: str
    target_files: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


@dataclass
class WorkerReport:
    status: str = "partial"
    summary: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    verification_result: str = "not_run"
    likely_files: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    remaining_risks: list[str] = field(default_factory=list)
    next_recommendation: str = ""
    raw_output: str = ""


@dataclass
class CandidatePatch:
    worker_id: str
    patch_unit: PatchUnit
    report: WorkerReport
    diff_path: Path | None = None
    diff_text: str = ""
    files_changed: list[str] = field(default_factory=list)


@dataclass
class VerificationRecord:
    candidate_worker_id: str
    command: str
    passed: bool
    exit_code: int | None
    stdout_path: str | None = None
    stderr_path: str | None = None
    metadata_path: str | None = None


@dataclass
class OrchestrateState:
    original_task: str
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    repo_facts: list[str] = field(default_factory=list)
    files_in_scope: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    current_frontier: list[str] = field(default_factory=list)
    completed_rounds: int = 0
    merged_patches: list[dict[str, Any]] = field(default_factory=list)
    verification_history: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> OrchestrateState:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(**raw)
