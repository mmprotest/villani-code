from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

AcceptanceProbeStatus = Literal[
    "not_applicable",
    "not_defined",
    "defined",
    "runnable",
    "running",
    "failed",
    "passed",
    "stale",
]

_TERMINAL_OUTPUT_LIMIT = 6000
_CONTEXT_OUTPUT_LIMIT = 3500


@dataclass(slots=True)
class AcceptanceProbeEvent:
    seq: int
    type: str
    status: str
    command: str | None = None
    exit_code: int | None = None
    summary: str | None = None
    evidence: str | None = None
    output: str | None = None
    reason: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(slots=True)
class AcceptanceProbeState:
    acceptance_probe_commands: list[str] = field(default_factory=list)
    acceptance_probe_description: str | None = None
    acceptance_probe_status: AcceptanceProbeStatus = "not_defined"
    acceptance_probe_required: bool = False
    acceptance_probe_attempt_count: int = 0
    last_probe_command: str | None = None
    last_probe_output: str | None = None
    last_probe_exit_code: int | None = None
    last_probe_failure_summary: str | None = None
    last_probe_pass_evidence: str | None = None
    probe_not_applicable_reason: str | None = None
    events: list[AcceptanceProbeEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _append_event(self, event_type: str, **kwargs: Any) -> None:
        self.events.append(
            AcceptanceProbeEvent(
                seq=len(self.events) + 1,
                type=event_type,
                status=self.acceptance_probe_status,
                **kwargs,
            )
        )

    def define(self, commands: list[str], description: str | None, runnable: bool = True) -> None:
        cleaned = [str(command).strip() for command in commands if str(command).strip()]
        if not cleaned:
            raise ValueError("acceptance probe definition requires at least one command")
        self.acceptance_probe_commands = cleaned
        self.acceptance_probe_description = str(description or "").strip() or None
        self.acceptance_probe_status = "runnable" if runnable else "defined"
        self.acceptance_probe_required = True
        self.probe_not_applicable_reason = None
        self._append_event("defined", summary=self.acceptance_probe_description)

    def mark_not_applicable(self, reason: str) -> None:
        cleaned = str(reason or "").strip()
        if not cleaned:
            raise ValueError("not-applicable acceptance probe requires a non-empty reason")
        self.acceptance_probe_commands = []
        self.acceptance_probe_description = None
        self.acceptance_probe_status = "not_applicable"
        self.acceptance_probe_required = False
        self.probe_not_applicable_reason = cleaned
        self._append_event("not_applicable", reason=cleaned)

    def mark_stale(self, reason: str) -> None:
        if self.acceptance_probe_status in {"passed", "failed", "runnable", "defined"}:
            self.acceptance_probe_status = "stale"
            self._append_event("stale", reason=str(reason or "workspace mutation"))

    def matches_command(self, command: str) -> bool:
        normalized = _normalize_command(command)
        return bool(normalized) and any(_normalize_command(c) == normalized for c in self.acceptance_probe_commands)

    def record_execution(self, command: str, exit_code: int, stdout: str = "", stderr: str = "") -> None:
        self.acceptance_probe_attempt_count += 1
        self.last_probe_command = command
        self.last_probe_exit_code = int(exit_code)
        output = _bounded_output(stdout, stderr, _TERMINAL_OUTPUT_LIMIT)
        self.last_probe_output = output
        if int(exit_code) == 0:
            self.acceptance_probe_status = "passed"
            self.last_probe_failure_summary = None
            self.last_probe_pass_evidence = _summarize_pass(command, output)
            self._append_event(
                "passed",
                command=command,
                exit_code=int(exit_code),
                evidence=self.last_probe_pass_evidence,
                output=output,
            )
        else:
            self.acceptance_probe_status = "failed"
            self.last_probe_failure_summary = summarize_probe_failure(command, int(exit_code), output)
            self.last_probe_pass_evidence = None
            self._append_event(
                "failed",
                command=command,
                exit_code=int(exit_code),
                summary=self.last_probe_failure_summary,
                output=output,
            )

    def completion_blocker(self) -> str | None:
        if self.acceptance_probe_status == "not_applicable":
            if self.probe_not_applicable_reason:
                return None
            return "Acceptance probe is marked not applicable but no reason was recorded."
        if not self.acceptance_probe_required:
            return None
        if self.acceptance_probe_status == "passed":
            return None
        if self.acceptance_probe_status == "not_defined":
            return "Define the smallest executable acceptance probe, or mark it not applicable with a reason, before completing."
        if self.acceptance_probe_status in {"defined", "runnable", "stale"}:
            return "Run the acceptance probe before completing."
        if self.acceptance_probe_status == "failed":
            return "Repair the observed acceptance-probe failure and rerun the probe before completing."
        return f"Acceptance probe status is {self.acceptance_probe_status}; completion requires passed."

    def failed_probe_context(self) -> str:
        command = self.last_probe_command or ""
        summary = self.last_probe_failure_summary or "Probe failed."
        output = _truncate(self.last_probe_output or "", _CONTEXT_OUTPUT_LIMIT)
        return (
            "The acceptance probe failed. Repair the observed failure before broad exploration.\n"
            f"Probe command:\n{command}\n\n"
            f"Failure summary:\n{summary}\n\n"
            f"Relevant output:\n{output}\n\n"
            "After making a repair, rerun the acceptance probe."
        )


def _normalize_command(command: str) -> str:
    return " ".join(str(command or "").strip().split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(0, limit // 2)
    tail = max(0, limit - head - 80)
    return f"{text[:head]}\n... [truncated {len(text) - head - tail} chars] ...\n{text[-tail:]}"


def _bounded_output(stdout: str, stderr: str, limit: int) -> str:
    parts = []
    if stdout:
        parts.append(f"STDOUT:\n{stdout}")
    if stderr:
        parts.append(f"STDERR:\n{stderr}")
    return _truncate("\n".join(parts), limit)


def summarize_probe_failure(command: str, exit_code: int, output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    signal = ""
    for line in reversed(lines[-80:]):
        lowered = line.lower()
        if any(token in lowered for token in ("assert", "error", "failed", "traceback", "exception", "expected")):
            signal = line
            break
    if not signal and lines:
        signal = lines[-1]
    return _truncate(f"{command} exited {exit_code}" + (f": {signal}" if signal else ""), 700)


def _summarize_pass(command: str, output: str) -> str:
    excerpt = " ".join(line.strip() for line in output.splitlines() if line.strip())[:500]
    return f"{command} exited 0" + (f"; evidence: {excerpt}" if excerpt else "")


def parse_bash_result(content: str) -> tuple[str, int, str, str] | None:
    try:
        payload = json.loads(str(content))
    except Exception:
        return None
    if not isinstance(payload, dict) or "command" not in payload or "exit_code" not in payload:
        return None
    return (
        str(payload.get("command", "")),
        int(payload.get("exit_code", 0)),
        str(payload.get("stdout", "")),
        str(payload.get("stderr", "")),
    )
