from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from villani_code.benchmark.models import VerificationOutcome


def _normalize_verification_command(command: str) -> tuple[list[str] | str, str, bool]:
    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return command, command, True
    if not tokens:
        return command, command, True

    executable = Path(tokens[0]).name.lower()
    if executable in {"pytest", "pytest.exe"}:
        normalized = [sys.executable, "-m", "pytest", *tokens[1:]]
        return normalized, " ".join(normalized), False
    if executable in {"python", "python.exe"} and len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] == "pytest":
        return command, command, True
    return command, command, True


def _is_launch_failure(exit_code: int | None, stderr: str) -> bool:
    if exit_code not in {127, 9009}:
        return False
    err = stderr.lower()
    return "not found" in err or "not recognized" in err


def run_commands(
    repo: Path,
    commands: list[str],
    timeout_seconds: int,
    *,
    stage: str = "verification",
    logger: Callable[[str], None] | None = None,
    artifact_dir: Path | None = None,
) -> tuple[bool, list[VerificationOutcome], float | None, float | None, bool]:
    outcomes: list[VerificationOutcome] = []
    first_verify: float | None = None
    last_verify: float | None = None
    launch_failed = False
    for idx, command in enumerate(commands, start=1):
        started = time.monotonic()
        if first_verify is None:
            first_verify = started
        normalized_command, normalized_display, shell = _normalize_verification_command(command)
        if logger is not None:
            logger(f"{stage} verify cmd={command}")
            logger(f"{stage} verify normalized={normalized_display}")
        try:
            proc = subprocess.run(
                normalized_command,
                cwd=repo,
                shell=shell,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            passed = proc.returncode == 0
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
            if not passed and _is_launch_failure(exit_code, stderr):
                launch_failed = True
        except subprocess.TimeoutExpired as exc:
            passed = False
            exit_code = None
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + "\n[timeout]"
        except OSError as exc:
            passed = False
            exit_code = None
            stdout = ""
            stderr = f"[launch-error] {exc}"
            launch_failed = True
            if logger is not None:
                logger(f"{stage} verify launch-failed={exc}")
        finished = time.monotonic()
        last_verify = finished
        stdout_artifact: str | None = None
        stderr_artifact: str | None = None
        metadata_artifact: str | None = None
        if artifact_dir is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            stage_slug = stage.lower().replace("(", "_").replace(")", "").replace(" ", "_").replace("-", "_")
            stdout_path = artifact_dir / f"{stage_slug}_verify_{idx}_stdout.txt"
            stderr_path = artifact_dir / f"{stage_slug}_verify_{idx}_stderr.txt"
            metadata_path = artifact_dir / f"{stage_slug}_verify_{idx}_meta.json"
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "stage": stage,
                        "index": idx,
                        "command": command,
                        "normalized_command": normalized_display,
                        "exit_code": exit_code,
                        "runtime_seconds": round(finished - started, 6),
                        "passed": passed,
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            stdout_artifact = str(stdout_path)
            stderr_artifact = str(stderr_path)
            metadata_artifact = str(metadata_path)

        outcomes.append(
            VerificationOutcome(
                command=command,
                normalized_command=normalized_display,
                passed=passed,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                started_at=started,
                finished_at=finished,
                stdout_artifact=stdout_artifact,
                stderr_artifact=stderr_artifact,
                metadata_artifact=metadata_artifact,
            )
        )
        if not passed:
            return False, outcomes, first_verify, last_verify, launch_failed
    return True, outcomes, first_verify, last_verify, launch_failed
