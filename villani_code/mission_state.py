from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.utils import ensure_dir


@dataclass(slots=True)
class VerifiedFact:
    kind: str
    value: str
    source: str


@dataclass(slots=True)
class OpenHypothesis:
    hypothesis_id: str
    statement: str
    confidence: float
    status: str


@dataclass(slots=True)
class MissionStep:
    step_id: str
    label: str
    mode: str
    status: str
    target_files: list[str]
    verification_plan: list[str]
    outcome_summary: str = ""


@dataclass(slots=True)
class MissionState:
    mission_id: str
    objective: str
    mode: str
    repo_root: str
    status: str
    current_step_id: str = ""
    plan_summary: str = ""
    verified_facts: list[VerifiedFact] = field(default_factory=list)
    open_hypotheses: list[OpenHypothesis] = field(default_factory=list)
    steps: list[MissionStep] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    intended_targets: list[str] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    last_failed_command: str = ""
    last_failed_summary: str = ""
    last_checkpoint_id: str = ""
    last_transcript_path: str = ""
    compact_summary: str = ""
    autonomous_wave: int = 0
    autonomous_backlog_summary: list[str] = field(default_factory=list)
    autonomous_attempted_tasks: int = 0
    autonomous_satisfied_keys_summary: list[str] = field(default_factory=list)
    autonomous_blockers_summary: list[str] = field(default_factory=list)
    autonomous_stop_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MissionState":
        return cls(
            mission_id=str(payload.get("mission_id", "")),
            objective=str(payload.get("objective", "")),
            mode=str(payload.get("mode", "execution")),
            repo_root=str(payload.get("repo_root", "")),
            status=str(payload.get("status", "active")),
            current_step_id=str(payload.get("current_step_id", "")),
            plan_summary=str(payload.get("plan_summary", "")),
            verified_facts=[VerifiedFact(**item) for item in payload.get("verified_facts", [])],
            open_hypotheses=[OpenHypothesis(**item) for item in payload.get("open_hypotheses", [])],
            steps=[MissionStep(**item) for item in payload.get("steps", [])],
            changed_files=[str(v) for v in payload.get("changed_files", [])],
            intended_targets=[str(v) for v in payload.get("intended_targets", [])],
            validation_failures=[str(v) for v in payload.get("validation_failures", [])],
            last_failed_command=str(payload.get("last_failed_command", "")),
            last_failed_summary=str(payload.get("last_failed_summary", "")),
            last_checkpoint_id=str(payload.get("last_checkpoint_id", "")),
            last_transcript_path=str(payload.get("last_transcript_path", "")),
            compact_summary=str(payload.get("compact_summary", "")),
            autonomous_wave=int(payload.get("autonomous_wave", 0)),
            autonomous_backlog_summary=[str(v) for v in payload.get("autonomous_backlog_summary", [])],
            autonomous_attempted_tasks=int(payload.get("autonomous_attempted_tasks", 0)),
            autonomous_satisfied_keys_summary=[str(v) for v in payload.get("autonomous_satisfied_keys_summary", [])],
            autonomous_blockers_summary=[str(v) for v in payload.get("autonomous_blockers_summary", [])],
            autonomous_stop_reason=str(payload.get("autonomous_stop_reason", "")),
        )


def _missions_root(repo: Path) -> Path:
    return repo / ".villani_code" / "missions"


def get_mission_dir(repo: Path, mission_id: str) -> Path:
    return _missions_root(repo) / mission_id


def get_mission_state_path(repo: Path, mission_id: str) -> Path:
    return get_mission_dir(repo, mission_id) / "mission_state.json"


def get_current_mission_metadata_path(repo: Path) -> Path:
    return _missions_root(repo) / "current.json"


def new_mission_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_mission_state(repo: Path, objective: str, mode: str, mission_id: str | None = None) -> MissionState:
    resolved = repo.resolve()
    state = MissionState(
        mission_id=mission_id or new_mission_id(),
        objective=objective,
        mode=mode,
        repo_root=str(resolved),
        status="active",
    )
    save_mission_state(resolved, state)
    set_current_mission_id(resolved, state.mission_id)
    return state


def save_mission_state(repo: Path, mission_state: MissionState) -> Path:
    path = get_mission_state_path(repo.resolve(), mission_state.mission_id)
    ensure_dir(path.parent)
    path.write_text(json.dumps(mission_state.to_dict(), indent=2), encoding="utf-8")
    return path


def load_mission_state(repo: Path, mission_id: str) -> MissionState:
    path = get_mission_state_path(repo.resolve(), mission_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return MissionState.from_dict(payload)


def set_current_mission_id(repo: Path, mission_id: str) -> Path:
    path = get_current_mission_metadata_path(repo.resolve())
    ensure_dir(path.parent)
    path.write_text(json.dumps({"mission_id": mission_id}, indent=2), encoding="utf-8")
    return path


def get_current_mission_id(repo: Path) -> str:
    path = get_current_mission_metadata_path(repo.resolve())
    if not path.exists():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("mission_id", ""))


def load_resume_bundle(repo: Path, mission_id: str) -> dict[str, Any]:
    mission_dir = get_mission_dir(repo.resolve(), mission_id)
    state = load_mission_state(repo, mission_id)
    messages_path = mission_dir / "messages.json"
    summary_path = mission_dir / "working_summary.md"
    return {
        "mission_state": state,
        "messages": json.loads(messages_path.read_text(encoding="utf-8")) if messages_path.exists() else [],
        "working_summary": summary_path.read_text(encoding="utf-8") if summary_path.exists() else "",
    }
