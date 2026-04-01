from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from villani_code.mission_state import get_current_mission_id, get_mission_dir, load_mission_state
from villani_code.utils import ensure_dir


OPTIONAL_FILES = [
    "mission_state.json",
    "messages.json",
    "runtime_events.jsonl",
    "event_digest.json",
    "working_summary.md",
    "plan_artifact.json",
]


def create_debug_bundle(repo: Path, mission_id: str | None = None) -> Path:
    resolved = repo.resolve()
    target_mission = mission_id or get_current_mission_id(resolved)
    if not target_mission:
        raise RuntimeError("No mission id available for debug bundle.")
    mission_dir = get_mission_dir(resolved, target_mission)
    out_dir = resolved / ".villani_code" / "debug"
    ensure_dir(out_dir)
    zip_path = out_dir / f"{target_mission}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in OPTIONAL_FILES:
            path = mission_dir / name
            if path.exists():
                zf.write(path, arcname=name)
        state_path = mission_dir / "mission_state.json"
        if state_path.exists():
            mission_state = load_mission_state(resolved, target_mission)
            if mission_state.last_transcript_path:
                transcript = Path(mission_state.last_transcript_path)
                if transcript.exists():
                    zf.write(transcript, arcname=f"transcripts/{transcript.name}")
        diff_summary = _build_diff_summary(resolved)
        zf.writestr("repo_diff_summary.json", json.dumps(diff_summary, indent=2))
    return zip_path


def _build_diff_summary(repo: Path) -> dict[str, Any]:
    changed: list[str] = []
    for path in repo.rglob("*"):
        if ".git" in path.parts or ".villani_code" in path.parts:
            continue
        if path.is_file():
            continue
    return {"note": "Use git for full diff details", "repo": str(repo), "changed_files_count_hint": len(changed)}
