from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from villani_code.shells import run_portable_shell_command


def _fingerprint(stderr: str, stdout: str) -> str:
    body = (stderr or "")[:3000] + "\n" + (stdout or "")[:1200]
    return hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()[:12]


@dataclass(slots=True)
class ValidationDelta:
    status: str
    failed_delta: int
    passed_delta: int
    newly_passing_commands: list[str]
    newly_failing_commands: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_validation_commands(repo_root: str, commands: list[str], timeout: int = 120) -> list[dict[str, Any]]:
    repo = Path(repo_root)
    results: list[dict[str, Any]] = []
    for cmd in commands:
        try:
            proc = run_portable_shell_command(cmd, cwd=repo, timeout=timeout)
            normalized = {
                "command": cmd,
                "exit": int(proc.returncode),
                "stdout": (proc.stdout or "")[:4000],
                "stderr": (proc.stderr or "")[:4000],
                "failure_fingerprint": _fingerprint(proc.stderr, proc.stdout) if proc.returncode != 0 else "",
            }
        except TimeoutError:
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


def compute_validation_delta(
    baseline_summary: dict[str, Any],
    previous_command_results: list[dict[str, Any]],
    current_command_results: list[dict[str, Any]],
) -> ValidationDelta:
    previous_failed = int((baseline_summary or {}).get("failed", 0) or 0)
    previous_passed = int((baseline_summary or {}).get("passed", 0) or 0)
    current_summary = summarize_validation_results(current_command_results)
    current_failed = int(current_summary.get("failed", 0) or 0)
    current_passed = int(current_summary.get("passed", 0) or 0)
    before_by_cmd = {str(item.get("command", "")): int(item.get("exit", 1)) for item in previous_command_results}
    after_by_cmd = {str(item.get("command", "")): int(item.get("exit", 1)) for item in current_command_results}
    improved: list[str] = []
    worsened: list[str] = []
    for cmd in sorted(set(before_by_cmd) | set(after_by_cmd)):
        before = before_by_cmd.get(cmd, 1)
        after = after_by_cmd.get(cmd, 1)
        if before != 0 and after == 0:
            improved.append(cmd)
        elif before == 0 and after != 0:
            worsened.append(cmd)
    if improved and not worsened:
        status = "improved"
    elif worsened and not improved:
        status = "worsened"
    elif improved and worsened:
        status = "partially_improved"
    else:
        status = "unchanged"
    return ValidationDelta(
        status=status,
        failed_delta=previous_failed - current_failed,
        passed_delta=current_passed - previous_passed,
        newly_passing_commands=improved,
        newly_failing_commands=worsened,
    )


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
