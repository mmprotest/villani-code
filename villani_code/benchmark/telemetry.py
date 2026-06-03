from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from villani_code.atif_export import write_atif_trajectory
from villani_code.trace_summary import normalize_token_usage

REQUIRED_ARTIFACTS = [
    "telemetry.json", "full_transcript.json", "trajectory.json", "events.jsonl",
    "model_requests.jsonl", "model_responses.jsonl", "agent_stdout.txt",
    "agent_stderr.txt", "agent_run_meta.json",
]
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|bearer)\s*[:=]\s*[^\s,}]+")


def sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key_l = str(k).lower()
            if any(s in key_l for s in ("api_key", "apikey", "auth_token", "access_token", "secret", "password", "authorization")):
                out[k] = "[REDACTED]"
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_RE.sub(lambda m: m.group(1) + "=[REDACTED]", obj)
    return obj


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def copy_if_exists(src: Path | None, dst: Path) -> None:
    if src and src.exists():
        dst.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    elif not dst.exists():
        dst.write_text("", encoding="utf-8")


def find_current_mission_dir(repo: Path) -> Path | None:
    current = repo / ".villani_code" / "missions" / "current.json"
    if current.exists():
        try:
            mid = json.loads(current.read_text(encoding="utf-8")).get("mission_id")
            if isinstance(mid, str) and mid:
                mission = repo / ".villani_code" / "missions" / mid
                if mission.exists():
                    return mission
        except Exception:
            pass
    missions = repo / ".villani_code" / "missions"
    if missions.exists():
        candidates = [p for p in missions.iterdir() if p.is_dir()]
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def find_debug_run_dir(attempt_dir: Path, mission_dir: Path | None) -> Path | None:
    if mission_dir is not None:
        candidate = attempt_dir / mission_dir.name
        if candidate.exists():
            return candidate
    dirs = [p for p in attempt_dir.iterdir() if p.is_dir()] if attempt_dir.exists() else []
    return max(dirs, key=lambda p: p.stat().st_mtime) if dirs else None


def collect_usage_and_elapsed(model_responses_path: Path) -> tuple[dict[str, int | None | str], float | None]:
    total_in = total_out = total_total = 0
    exact = True
    saw_response = False
    elapsed = 0.0
    any_elapsed = False
    for row in read_jsonl(model_responses_path):
        saw_response = True
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if "error_type" in payload:
            exact = False
        usage = normalize_token_usage(payload)
        if usage["tokens_input"] is None or usage["tokens_output"] is None or usage["tokens_total"] is None:
            exact = False
        else:
            total_in += int(usage["tokens_input"])
            total_out += int(usage["tokens_output"])
            total_total += int(usage["tokens_total"])
        if isinstance(payload.get("elapsed_seconds"), (int, float)):
            elapsed += float(payload["elapsed_seconds"])
            any_elapsed = True
    if not exact or not saw_response:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "quality": "unavailable"}, elapsed if any_elapsed else None
    return {"input_tokens": total_in, "output_tokens": total_out, "total_tokens": total_total, "quality": "exact"}, elapsed if any_elapsed else None


def finalize_attempt_artifacts(
    *,
    attempt_dir: Path,
    repo: Path,
    task_id: str,
    repeat_index: int,
    model: str | None,
    provider: str | None,
    agent_version: str | None,
    agent_process_elapsed_seconds: float | None,
    total_attempt_duration_seconds: float | None,
    verified_outcome: str,
    visible_pass: bool | None,
    hidden_pass: bool | None,
    timed_out: bool,
    termination_reason: str | None,
    exception_type: str | None = None,
    exception_message: str | None = None,
) -> dict[str, Any]:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    mission_dir = find_current_mission_dir(repo)
    debug_run_dir = find_debug_run_dir(attempt_dir, mission_dir)

    copy_if_exists((mission_dir / "runtime_events.jsonl") if mission_dir else None, attempt_dir / "events.jsonl")
    copy_if_exists((debug_run_dir / "model_requests.jsonl") if debug_run_dir else None, attempt_dir / "model_requests.jsonl")
    copy_if_exists((debug_run_dir / "model_responses.jsonl") if debug_run_dir else None, attempt_dir / "model_responses.jsonl")

    transcript_src = None
    if debug_run_dir and (debug_run_dir / "full_transcript.json").exists():
        transcript_src = debug_run_dir / "full_transcript.json"
    elif mission_dir and (mission_dir / "mission_state.json").exists():
        try:
            p = json.loads((mission_dir / "mission_state.json").read_text(encoding="utf-8")).get("last_transcript_path")
            if isinstance(p, str) and Path(p).exists():
                transcript_src = Path(p)
        except Exception:
            pass
    transcript: dict[str, Any] = {}
    if transcript_src:
        try:
            transcript = json.loads(transcript_src.read_text(encoding="utf-8"))
        except Exception:
            transcript = {}
    transcript.setdefault("instruction", "")
    transcript.setdefault("terminal_state", {"timed_out": timed_out, "verified_outcome": verified_outcome, "termination_reason": termination_reason})
    (attempt_dir / "full_transcript.json").write_text(json.dumps(sanitize(transcript), indent=2, ensure_ascii=False), encoding="utf-8")

    usage, local_elapsed = collect_usage_and_elapsed(attempt_dir / "model_responses.jsonl")
    attempt_id = f"{task_id}__r{repeat_index}"
    telemetry = {
        "schema_version": "villani.telemetry.v1",
        "attempt_id": attempt_id,
        "task_id": task_id,
        "repeat_index": repeat_index,
        "agent": {"name": "villani", "version": agent_version},
        "model": {"identifier": model, "provider": provider},
        "usage": usage,
        "timing": {
            "local_inference_elapsed_seconds": local_elapsed,
            "agent_process_elapsed_seconds": agent_process_elapsed_seconds,
            "total_attempt_duration_seconds": total_attempt_duration_seconds,
        },
        "outcome": {"verified_outcome": verified_outcome, "visible_pass": visible_pass, "hidden_pass": hidden_pass},
        "termination": {"timed_out": timed_out, "reason": termination_reason, "exception_type": exception_type, "exception_message": exception_message},
        "artifacts": {"full_transcript": "full_transcript.json", "atif_trajectory": "trajectory.json", "events": "events.jsonl", "model_requests": "model_requests.jsonl", "model_responses": "model_responses.jsonl"},
    }
    (attempt_dir / "telemetry.json").write_text(json.dumps(sanitize(telemetry), indent=2, ensure_ascii=False), encoding="utf-8")
    events = read_jsonl(attempt_dir / "events.jsonl")
    write_atif_trajectory(attempt_dir / "trajectory.json", transcript=sanitize(transcript), telemetry=sanitize(telemetry), events=sanitize(events))
    for name in REQUIRED_ARTIFACTS:
        p = attempt_dir / name
        if not p.exists():
            p.write_text("" if name.endswith((".txt", ".jsonl")) else "{}", encoding="utf-8")
    return telemetry
