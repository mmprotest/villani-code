from __future__ import annotations
import subprocess
import sys

def run_check(command: list[str]) -> int:
    proc = subprocess.run(command, text=True, capture_output=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return 0
