from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality


class AdapterEvent(BaseModel):
    type: str
    timestamp: float
    payload: dict[str, object] = Field(default_factory=dict)


class AdapterRunConfig(BaseModel):
    prompt: str
    workspace_repo: Path
    timeout_seconds: int
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class AdapterRunResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    timeout: bool
    runtime_seconds: float
    telemetry_quality: TelemetryQuality
    telemetry_field_quality_map: dict[str, FieldQuality] = Field(default_factory=dict)
    events: list[AdapterEvent] = Field(default_factory=list)


class AgentAdapter(ABC):
    name: str
    version = "1"
    capability = "coarse_wrapper"
    telemetry_capability = "coarse_process_only"
    fairness_classification: FairnessClassification = FairnessClassification.COARSE_WRAPPER_ONLY
    fairness_notes = "Command-wrapper adapter; outcome-only comparison is safer than process-level comparisons."
    command_capture: FieldQuality = FieldQuality.UNAVAILABLE
    file_event_capture: FieldQuality = FieldQuality.UNAVAILABLE
    verify_capture: FieldQuality = FieldQuality.INFERRED
    model_identity_known: bool = False
    provider_info_known: bool = False
    timeout_enforced_by_harness: bool = True

    @abstractmethod
    def build_command(self, config: AdapterRunConfig) -> list[str]: ...

    def _field_quality(self) -> dict[str, FieldQuality]:
        return {
            "num_shell_commands": self.command_capture,
            "num_failed_commands": self.command_capture,
            "touched_file_paths": self.file_event_capture,
            "time_to_first_edit": self.file_event_capture,
            "time_to_first_verify": self.verify_capture,
            "last_verification_time": self.verify_capture,
            "verifications_run": self.verify_capture,
            "verification_attempt_count": self.verify_capture,
            "expected_file_first_read_time": self.file_event_capture,
            "expected_files_found": FieldQuality.INFERRED,
            "expected_files_total": FieldQuality.EXACT,
            "touched_irrelevant_files": FieldQuality.INFERRED,
            "self_corrected_after_failed_verify": FieldQuality.INFERRED,
        }

    def run(self, config: AdapterRunConfig) -> AdapterRunResult:
        started = time.monotonic()
        cmd = self.build_command(config)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(cmd)})]
        proc = subprocess.Popen(cmd, cwd=config.workspace_repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
        try:
            stdout, stderr = proc.communicate(timeout=config.timeout_seconds)
            timeout = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timeout = True
        events.append(AdapterEvent(type="command_finished", timestamp=time.monotonic(), payload={"exit_code": proc.returncode if not timeout else None}))

        return AdapterRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if not timeout else None,
            timeout=timeout,
            runtime_seconds=time.monotonic() - started,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
            events=events,
        )


class VillaniAdapter(AgentAdapter):
    name = "villani"
    capability = "native_runtime_instrumented"
    telemetry_capability = "structured_runtime_events"
    fairness_classification = FairnessClassification.EXACT_COMPARABLE
    fairness_notes = "Villani adapter exposes structured runtime events and is directly comparable across Villani model variants."
    command_capture = FieldQuality.EXACT
    file_event_capture = FieldQuality.EXACT
    verify_capture = FieldQuality.EXACT
    model_identity_known = True
    provider_info_known = True

    def build_command(self, config: AdapterRunConfig) -> list[str]:
        if not config.model:
            raise ValueError("villani requires --model")
        command = [
            sys.executable,
            "-m",
            "villani_code.cli",
            "run",
            config.prompt,
            "--repo",
            str(config.workspace_repo),
            "--provider",
            "anthropic",
            "--model",
            config.model,
            "--no-stream",
            "--emit-runtime-events",
        ]
        if config.base_url:
            command.extend(["--base-url", config.base_url])
        if config.api_key:
            command.extend(["--api-key", config.api_key])
        return command

    def run(self, config: AdapterRunConfig) -> AdapterRunResult:
        base = super().run(config)
        events_file = config.workspace_repo / ".villani_code" / "runtime_events.jsonl"
        events: list[AdapterEvent] = []
        if events_file.exists():
            for raw in events_file.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                payload = json.loads(raw)
                events.append(AdapterEvent(type=str(payload.get("event", "model_message")), timestamp=float(payload.get("ts", time.time())), payload=payload))
        merged = base.events + events
        return AdapterRunResult(
            **base.model_dump(exclude={"events", "telemetry_quality", "telemetry_field_quality_map"}),
            events=merged,
            telemetry_quality=TelemetryQuality.EXACT if events else TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
        )


class TemplateCliAdapter(AgentAdapter):
    template: list[str]
    capability = "cli_wrapper"
    telemetry_capability = "coarse_process_only"
    fairness_classification = FairnessClassification.COARSE_WRAPPER_ONLY
    fairness_notes = "External CLI wrapper without benchmark-native event capture; process telemetry is coarse and only approximately comparable."

    def build_command(self, config: AdapterRunConfig) -> list[str]:
        command = list(self.template)
        command.append(config.prompt)
        return command


class ClaudeCodeAdapter(TemplateCliAdapter):
    name = "claude"
    template = ["claude", "-p"]


class OpenCodeAdapter(TemplateCliAdapter):
    name = "opencode"
    template = ["opencode", "run", "--prompt"]


class CopilotCliAdapter(TemplateCliAdapter):
    name = "copilot-cli"
    template = ["copilot", "suggest"]


class CommandAdapter(AgentAdapter):
    name = "cmd"
    capability = "raw_command"
    telemetry_capability = "none"
    fairness_classification = FairnessClassification.NOT_COMPARABLE
    fairness_notes = "Arbitrary shell command adapter for smoke tests/debugging; not a fair agent comparison target."

    def __init__(self, command: str) -> None:
        self.command = command

    def build_command(self, config: AdapterRunConfig) -> list[str]:
        return shlex.split(self.command) + [config.prompt]
