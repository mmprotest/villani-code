from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from villani_code.mission import Mission


def ensure_mission_dir(repo_root: str) -> Path:
    d = Path(repo_root) / ".villani" / "missions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mission_dir(repo_root: str, mission_id: str) -> Path:
    d = ensure_mission_dir(repo_root) / mission_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_mission_snapshot(repo_root: str, mission: Mission, execution_state: dict[str, Any] | None = None) -> Path:
    mdir = _mission_dir(repo_root, mission.mission_id)
    payload = {"mission": mission.to_dict(), "execution_state": execution_state or {}, "timestamp": datetime.now(timezone.utc).isoformat()}
    path = mdir / "snapshot.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def append_mission_event(repo_root: str, mission_id: str, event: dict[str, Any]) -> Path:
    mdir = _mission_dir(repo_root, mission_id)
    path = mdir / "events.jsonl"
    line = dict(event)
    line.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    return path


def save_final_mission_report(repo_root: str, mission: Mission, report: dict[str, Any]) -> Path:
    mdir = _mission_dir(repo_root, mission.mission_id)
    payload = {
        "mission": mission.to_dict(),
        "summary": report,
        "stop_reason": mission.stop_reason,
        "final_outcome": mission.final_outcome,
        "files_touched": report.get("files_touched", []),
        "evidence": report.get("evidence", []),
    }
    path = mdir / "final_report.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_mission(repo_root: str, mission_id: str) -> dict[str, Any] | None:
    path = _mission_dir(repo_root, mission_id) / "snapshot.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_recent_missions(repo_root: str, limit: int = 20) -> list[dict[str, Any]]:
    root = ensure_mission_dir(repo_root)
    items: list[dict[str, Any]] = []
    for mdir in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        snap = mdir / "snapshot.json"
        if not snap.exists():
            continue
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            mission = data.get("mission", {})
            items.append({
                "mission_id": mission.get("mission_id", mdir.name),
                "user_goal": mission.get("user_goal", ""),
                "mission_type": mission.get("mission_type", ""),
                "state": mission.get("state", ""),
                "updated_at": mission.get("updated_at", ""),
            })
        except Exception:
            continue
    return items
