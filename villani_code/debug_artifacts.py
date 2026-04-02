from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from villani_code.utils import ensure_dir


@dataclass(slots=True)
class DebugRunArtifacts:
    root: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.root / self.run_id

    def path(self, name: str) -> Path:
        return self.run_dir / name


DEBUG_JSONL_FILES = {
    "events": "events.jsonl",
    "turns": "turns.jsonl",
    "tool_calls": "tool_calls.jsonl",
    "commands": "commands.jsonl",
    "patches": "patches.jsonl",
    "approvals": "approvals.jsonl",
    "mission_state_snapshots": "mission_state_snapshots.jsonl",
    "validations": "validations.jsonl",
    "model_requests": "model_requests.jsonl",
    "model_responses": "model_responses.jsonl",
}


def resolve_debug_root(debug_root: Path | None = None) -> Path:
    if debug_root is not None:
        return debug_root
    return Path(tempfile.gettempdir()) / "villani-code-debug"


def create_debug_run_artifacts(run_id: str, debug_root: Path | None = None) -> DebugRunArtifacts:
    root = resolve_debug_root(debug_root)
    ensure_dir(root)
    artifacts = DebugRunArtifacts(root=root, run_id=run_id)
    ensure_dir(artifacts.run_dir)
    return artifacts


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)
