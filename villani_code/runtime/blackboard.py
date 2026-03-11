from __future__ import annotations

import json
from pathlib import Path

from villani_code.runtime.schemas import Blackboard
from villani_code.utils import ensure_dir


class BlackboardStore:
    def __init__(self, repo_root: Path, run_id: str) -> None:
        self.run_dir = repo_root / ".villani_code" / "runs" / run_id
        self.attempts_dir = self.run_dir / "attempts"
        ensure_dir(self.attempts_dir)
        self.events_path = self.run_dir / "events.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.blackboard_path = self.run_dir / "blackboard.json"

    def write(self, board: Blackboard) -> None:
        self.blackboard_path.write_text(board.model_dump_json(indent=2), encoding="utf-8")

    def append_event(self, event: dict[str, object]) -> None:
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event))
            handle.write("\n")

    def write_summary(self, summary: dict[str, object]) -> None:
        self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
