from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


def _fingerprint(stderr: str, stdout: str) -> str:
    body = (stderr or "")[:3000] + "\n" + (stdout or "")[:1200]
    return hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()[:12]


def run_validation_commands(repo_root: str, commands: list[str], timeout: int = 120) -> list[dict[str, Any]]:
    repo = Path(repo_root)
    results: list[dict[str, Any]] = []
    for cmd in commands:
        try:
            proc = subprocess.run(["bash", "-lc", cmd], cwd=repo, capture_output=True, text=True, timeout=timeout)
            normalized = {
                "command": cmd,
                "exit": int(proc.returncode),
                "stdout": (proc.stdout or "")[:4000],
                "stderr": (proc.stderr or "")[:4000],
                "failure_fingerprint": _fingerprint(proc.stderr, proc.stdout) if proc.returncode != 0 else "",
            }
        except subprocess.TimeoutExpired:
            normalized = {
                "command": cmd,
                "exit": 124,
                "stdout": "",
                "stderr": "command timed out",
                "failure_fingerprint": _fingerprint("command timed out", ""),
                "timed_out": True,
            }
        results.append(normalized)
    seen: dict[str, int] = {}
    for r in results:
        fp = str(r.get("failure_fingerprint", ""))
        if not fp:
            r["repeated_failure"] = False
            continue
        seen[fp] = seen.get(fp, 0) + 1
        r["repeated_failure"] = seen[fp] > 1
    return results


def summarize_validation_results(command_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not command_results:
        return {
            "commands_run": 0,
            "passed": 0,
            "failed": 0,
            "all_passed": False,
            "any_failed": False,
            "failure_fingerprints": [],
        }
    passed = sum(1 for r in command_results if int(r.get("exit", 1)) == 0)
    failed = len(command_results) - passed
    fps = [str(r.get("failure_fingerprint", "")) for r in command_results if r.get("failure_fingerprint")]
    return {
        "commands_run": len(command_results),
        "passed": passed,
        "failed": failed,
        "all_passed": failed == 0,
        "any_failed": failed > 0,
        "failure_fingerprints": sorted(set(fps)),
        "commands": [str(r.get("command", "")) for r in command_results],
    }
