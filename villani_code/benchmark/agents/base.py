from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality


class AgentRunner(ABC):
    name: str
    version = "1"
    capability = "cli_wrapper"
    telemetry_capability = "coarse_process_only"
    fairness_classification: FairnessClassification = FairnessClassification.COARSE_WRAPPER_ONLY
    fairness_notes = "External CLI wrapper without benchmark-native event capture; process telemetry is coarse and only approximately comparable."
    command_capture: FieldQuality = FieldQuality.UNAVAILABLE
    file_event_capture: FieldQuality = FieldQuality.UNAVAILABLE
    verify_capture: FieldQuality = FieldQuality.INFERRED
    supports_model_override: bool = True

    @abstractmethod
    def build_command(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
    ) -> list[str]:
        raise NotImplementedError

    def build_env(self, *, base_url: str | None, api_key: str | None) -> dict[str, str]:
        return os.environ.copy()

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
            "tool_calls_total": self.file_event_capture,
            "file_reads": self.file_event_capture,
            "file_writes": self.file_event_capture,
            "patch_attempts": self.file_event_capture,
            "test_runs": self.verify_capture,
            "retries_after_failure": FieldQuality.INFERRED,
            "number_of_turns": self.file_event_capture,
            "tokens_input": FieldQuality.UNAVAILABLE,
            "tokens_output": FieldQuality.UNAVAILABLE,
            "total_tokens": FieldQuality.UNAVAILABLE,
            "estimated_cost": FieldQuality.UNAVAILABLE,
        }

    def run_agent(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        timeout: int,
    ) -> AdapterRunResult:
        started = time.monotonic()
        command = self.build_command(repo_path, prompt, model, base_url, api_key, provider)
        env = self.build_env(base_url=base_url, api_key=api_key)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(command)})]
        proc = subprocess.Popen(command, cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timeout_hit = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timeout_hit = True
        events.append(AdapterEvent(type="command_finished", timestamp=time.monotonic(), payload={"exit_code": proc.returncode if not timeout_hit else None}))
        return AdapterRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if not timeout_hit else None,
            timeout=timeout_hit,
            runtime_seconds=time.monotonic() - started,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
            events=events,
        )
