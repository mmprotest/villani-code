from __future__ import annotations

import subprocess
import time
from pathlib import Path

from villani_code.benchmark.models import VerificationOutcome


def run_commands(repo: Path, commands: list[str], timeout_seconds: int) -> tuple[bool, list[VerificationOutcome], float | None]:
    outcomes: list[VerificationOutcome] = []
    first_verify: float | None = None
    for command in commands:
        if first_verify is None:
            first_verify = time.monotonic()
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
        outcomes.append(
            VerificationOutcome(
                command=command,
                passed=passed,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
        )
        if not passed:
            return False, outcomes, first_verify
    return True, outcomes, first_verify
