from __future__ import annotations

import subprocess
import time
from pathlib import Path

from villani_code.benchmark.models import VerificationOutcome


def run_commands(repo: Path, commands: list[str], timeout_seconds: int) -> tuple[bool, list[VerificationOutcome], float | None, float | None]:
    outcomes: list[VerificationOutcome] = []
    first_verify: float | None = None
    last_verify: float | None = None
    for command in commands:
        started = time.monotonic()
        if first_verify is None:
            first_verify = started
        try:
            proc = subprocess.run(
                command,
                cwd=repo,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            passed = proc.returncode == 0
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            passed = False
            exit_code = None
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + "\n[timeout]"
        finished = time.monotonic()
        last_verify = finished
        outcomes.append(
            VerificationOutcome(
                command=command,
                passed=passed,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                started_at=started,
                finished_at=finished,
            )
        )
        if not passed:
            return False, outcomes, first_verify, last_verify
    return True, outcomes, first_verify, last_verify
