from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from villani_code.orchestrate.state import WorkerReport


@dataclass
class WorkerConfig:
    base_url: str
    model: str
    provider: str
    api_key: str | None
    timeout_seconds: int


@dataclass
class WorkerRunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    report: WorkerReport


_FILE_RE = re.compile(r"\b[\w./-]+\.[\w]+\b")
_CMD_RE = re.compile(r"(?m)^\$\s+(.+)$")


def parse_worker_report(output: str) -> WorkerReport:
    marker = "WORKER_REPORT_JSON"
    block = output
    if marker in output:
        block = output.split(marker, 1)[1].strip()
    start = block.find("{")
    end = block.rfind("}")
    if start >= 0 and end > start:
        candidate = block[start : end + 1]
        try:
            raw = json.loads(candidate)
            return WorkerReport(
                status=str(raw.get("status", "partial")),
                summary=str(raw.get("summary", "")),
                evidence=list(raw.get("evidence", [])) if isinstance(raw.get("evidence", []), list) else [],
                files_read=list(raw.get("files_read", [])) if isinstance(raw.get("files_read", []), list) else [],
                files_changed=list(raw.get("files_changed", [])) if isinstance(raw.get("files_changed", []), list) else [],
                commands_run=list(raw.get("commands_run", [])) if isinstance(raw.get("commands_run", []), list) else [],
                tests_run=list(raw.get("tests_run", [])) if isinstance(raw.get("tests_run", []), list) else [],
                verification_result=str(raw.get("verification_result", "not_run")),
                likely_files=list(raw.get("likely_files", [])) if isinstance(raw.get("likely_files", []), list) else [],
                hypotheses=list(raw.get("hypotheses", [])) if isinstance(raw.get("hypotheses", []), list) else [],
                remaining_risks=list(raw.get("remaining_risks", [])) if isinstance(raw.get("remaining_risks", []), list) else [],
                next_recommendation=str(raw.get("next_recommendation", "")),
                raw_output=output,
            )
        except json.JSONDecodeError:
            pass

    files = sorted(set(_FILE_RE.findall(output)))[:30]
    commands = [c.strip() for c in _CMD_RE.findall(output)][:20]
    status = "failed" if "traceback" in output.lower() else "partial"
    return WorkerReport(
        status=status,
        summary="Failed to parse WORKER_REPORT_JSON; generated fallback report.",
        files_read=files,
        likely_files=files[:10],
        commands_run=commands,
        raw_output=output,
    )


def run_worker(*, repo: Path, prompt: str, config: WorkerConfig) -> WorkerRunResult:
    cmd = [
        sys.executable,
        "-m",
        "villani_code.cli",
        "run",
        prompt,
        "--repo",
        str(repo),
        "--provider",
        config.provider,
        "--model",
        config.model,
        "--base-url",
        config.base_url,
        "--no-stream",
        "--auto-approve",
    ]
    if config.api_key:
        cmd.extend(["--api-key", config.api_key])

    try:
        proc = subprocess.run(cmd, cwd=repo, text=True, capture_output=True, timeout=config.timeout_seconds, check=False)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return WorkerRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            timed_out=False,
            report=parse_worker_report(combined),
        )
    except subprocess.TimeoutExpired as exc:
        combined = (exc.stdout or "") + "\n" + (exc.stderr or "") + "\n[timeout]"
        return WorkerRunResult(
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + "\n[timeout]",
            timed_out=True,
            report=WorkerReport(status="failed", summary="Worker timed out", raw_output=combined),
        )
