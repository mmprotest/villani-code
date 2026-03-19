from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Mapping

from villani_code.swebench_live.types import InstanceLogRecord, ProcessResult


SENSITIVE_ENV_TOKENS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def sanitize_command(command: list[str]) -> list[str]:
    sanitized: list[str] = []
    redact_next = False
    for part in command:
        if redact_next:
            sanitized.append("[REDACTED]")
            redact_next = False
            continue
        sanitized.append(part)
        if part == "--api-key":
            redact_next = True
    return sanitized


def sanitize_env(env: Mapping[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in env.items():
        if any(token in key.upper() for token in SENSITIVE_ENV_TOKENS):
            sanitized[key] = "[REDACTED]"
        else:
            sanitized[key] = value
    return sanitized


def run_logged_subprocess(
    command: list[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
) -> ProcessResult:
    _ensure_parent(stdout_path)
    _ensure_parent(stderr_path)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if stderr:
            stderr = f"{stderr}\n[timeout]"
        else:
            stderr = "[timeout]"
        exit_code = None
        timed_out = True
    duration = time.monotonic() - started
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return ProcessResult(
        command=command,
        sanitized_command=sanitize_command(command),
        exit_code=exit_code,
        duration_seconds=duration,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
    )


def write_predictions(predictions: Mapping[str, Mapping[str, str]], output_path: Path) -> None:
    _ensure_parent(output_path)
    output_path.write_text(json.dumps(predictions, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_sidecar_logs(records: list[InstanceLogRecord], logs_path: Path) -> None:
    _ensure_parent(logs_path)
    with logs_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
