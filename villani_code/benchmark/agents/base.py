from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
    fairness_notes = "Shared benchmark contract and harness-only scoring are used, but this adapter remains a coarse CLI wrapper with limited telemetry."
    command_capture: FieldQuality = FieldQuality.UNAVAILABLE
    file_event_capture: FieldQuality = FieldQuality.UNAVAILABLE
    verify_capture: FieldQuality = FieldQuality.INFERRED
    supports_model_override: bool = True

    def render_launch_prompt(self, benchmark_prompt: str) -> str:
        """Return the exact prompt text passed to the agent CLI/runtime."""
        return benchmark_prompt

    @abstractmethod
    def build_command(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        benchmark_config_json: str | None = None,
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

    @staticmethod
    def _is_sensitive_env_key(key: str) -> bool:
        normalized = key.upper()
        return any(token in normalized for token in ("KEY", "TOKEN", "SECRET", "PASSWORD"))

    def _write_debug_artifacts(
        self,
        debug_dir: Path,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        stdout: str,
        stderr: str,
        exit_code: int | None,
        timeout_hit: bool,
        runtime_seconds: float,
    ) -> dict[str, str]:
        debug_dir.mkdir(parents=True, exist_ok=True)
        command_path = debug_dir / "agent_command.txt"
        stdout_path = debug_dir / "agent_stdout.txt"
        stderr_path = debug_dir / "agent_stderr.txt"
        meta_path = debug_dir / "agent_run_meta.json"

        command_path.write_text(" ".join(command) + "\n", encoding="utf-8")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

        selected_env = {
            key: ("[REDACTED]" if self._is_sensitive_env_key(key) else value)
            for key, value in env.items()
            if key.startswith(("ANTHROPIC_", "OPENAI_", "VILLANI_", "AIDER_", "CODEX_"))
        }
        meta_path.write_text(
            json.dumps(
                {
                    "command": command,
                    "cwd": str(cwd),
                    "env": selected_env,
                    "exit_code": exit_code,
                    "timeout": timeout_hit,
                    "runtime_seconds": runtime_seconds,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        return {
            "agent_command": str(command_path),
            "agent_stdout": str(stdout_path),
            "agent_stderr": str(stderr_path),
            "agent_run_meta": str(meta_path),
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
        benchmark_config_json: str | None = None,
        debug_dir: Path | None = None,
    ) -> AdapterRunResult:
        started = time.monotonic()
        launch_prompt = self.render_launch_prompt(prompt)
        command = self.build_command(repo_path, launch_prompt, model, base_url, api_key, provider, benchmark_config_json=benchmark_config_json)
        command = self._resolve_subprocess_command(command)
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
        runtime_seconds = time.monotonic() - started
        exit_code = proc.returncode if not timeout_hit else None
        events.append(AdapterEvent(type="command_finished", timestamp=time.monotonic(), payload={"exit_code": exit_code}))
        debug_artifacts: dict[str, str] = {}
        if debug_dir is not None:
            debug_artifacts = self._write_debug_artifacts(
                debug_dir,
                command=command,
                cwd=repo_path,
                env=env,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                timeout_hit=timeout_hit,
                runtime_seconds=runtime_seconds,
            )
        return AdapterRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timeout=timeout_hit,
            runtime_seconds=runtime_seconds,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map=self._field_quality(),
            events=events,
            debug_artifacts=debug_artifacts,
        )

    @staticmethod
    def _resolve_subprocess_command(command: list[str]) -> list[str]:
        if not command:
            return command
        executable = command[0]
        if os.path.isabs(executable) or os.sep in executable or (os.altsep and os.altsep in executable):
            return command

        is_windows = sys.platform.startswith("win")
        resolved = shutil.which(executable)
        if resolved is None and is_windows:
            for suffix in (".cmd", ".bat", ".exe"):
                resolved = shutil.which(f"{executable}{suffix}")
                if resolved:
                    break
        if not resolved:
            return command

        if is_windows and resolved.lower().endswith((".cmd", ".bat")):
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", resolved, *command[1:]]
        return [resolved, *command[1:]]
