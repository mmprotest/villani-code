from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class WorkingMemorySnapshot:
    repo_assessment: dict[str, Any] = field(default_factory=dict)
    backlog: list[dict[str, Any]] = field(default_factory=list)
    completed_tasks: list[dict[str, Any]] = field(default_factory=list)
    failed_tasks: list[dict[str, Any]] = field(default_factory=list)
    validation_receipts: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    rejected_hypotheses: list[str] = field(default_factory=list)
    next_recommended_actions: list[str] = field(default_factory=list)
    stop_reason: str = ""


class WorkingMemoryStore:
    def __init__(self, repo: Path) -> None:
        self.path = repo / ".villani_code" / "working_memory.json"

    def load(self) -> WorkingMemorySnapshot:
        if not self.path.exists():
            return WorkingMemorySnapshot()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return WorkingMemorySnapshot()
        return WorkingMemorySnapshot(**payload)

    def write(self, snapshot: WorkingMemorySnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(asdict(snapshot), indent=2, sort_keys=True),
            encoding="utf-8",
        )
