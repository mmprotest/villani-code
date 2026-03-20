from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality
from villani_code.benchmark.usage import extract_token_usage


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
    usage_capture: FieldQuality = FieldQuality.UNAVAILABLE
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
            "prompt_tokens": self.usage_capture,
            "completion_tokens": self.usage_capture,
            "tokens_input": self.usage_capture,
            "tokens_output": self.usage_capture,
            "total_tokens": self.usage_capture,
            "cached_tokens": self.usage_capture,
            "reasoning_tokens": self.usage_capture,
            "estimated_cost": FieldQuality.UNAVAILABLE,
        }

    @staticmethod
    def _text_process_kwargs() -> dict[str, str | bool]:
        return {
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }

    @staticmethod
    def _normalize_process_output(output: str | bytes | None) -> str:
        if output is None:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return output

    @staticmethod
    def _process_group_popen_kwargs() -> dict[str, object]:
        if os.name == "nt":
            return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        return {"start_new_session": True}

    @staticmethod
    def _append_stderr_note(stderr: str, note: str) -> str:
        return f"{stderr}\n{note}" if stderr else note

    def _terminate_process_tree(self, proc: subprocess.Popen[str | bytes], cleanup_timeout: int) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=cleanup_timeout,
                    check=False,
                )
                return
            except Exception:
                pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                return
            except Exception:
                pass

        try:
            proc.kill()
        except Exception:
            pass

    def _run_subprocess_with_timeout(
        self,
        *,
        command: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: int,
        stdin_input: str | None = None,
        capture_stdin: bool = False,
        cleanup_timeout: int = 2,
    ) -> tuple[str, str, bool, int | None]:
        popen_kwargs: dict[str, object] = {
            "cwd": cwd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": env,
            **self._text_process_kwargs(),
            **self._process_group_popen_kwargs(),
        }
        if capture_stdin:
            popen_kwargs["stdin"] = subprocess.PIPE

        proc = subprocess.Popen(command, **popen_kwargs)
        try:
            if stdin_input is None:
                stdout, stderr = proc.communicate(timeout=timeout)
            else:
                stdout, stderr = proc.communicate(input=stdin_input, timeout=timeout)
            timeout_hit = False
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as timeout_error:
            timeout_hit = True
            stdout = timeout_error.output
            stderr = timeout_error.stderr
            self._terminate_process_tree(proc, cleanup_timeout=cleanup_timeout)
            try:
                drain_stdout, drain_stderr = proc.communicate(timeout=cleanup_timeout)
                if stdout is None:
                    stdout = drain_stdout
                if stderr is None:
                    stderr = drain_stderr
            except subprocess.TimeoutExpired as drain_timeout:
                if stdout is None:
                    stdout = drain_timeout.output
                if stderr is None:
                    stderr = drain_timeout.stderr
                stderr = self._append_stderr_note(
                    self._normalize_process_output(stderr),
                    "[benchmark timeout cleanup] bounded drain expired; output may be truncated.",
                )
            except Exception:
                stderr = self._append_stderr_note(
                    self._normalize_process_output(stderr),
                    "[benchmark timeout cleanup] final output drain failed; output may be truncated.",
                )
            exit_code = None

        stdout_text = self._normalize_process_output(stdout)
        stderr_text = self._normalize_process_output(stderr)
        return stdout_text, stderr_text, timeout_hit, exit_code

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
        env = self.build_env(base_url=base_url, api_key=api_key)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(command)})]
        stdout, stderr, timeout_hit, exit_code = self._run_subprocess_with_timeout(
            command=command,
            cwd=repo_path,
            env=env,
            timeout=timeout,
        )
        runtime_seconds = time.monotonic() - started
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
            token_usage=extract_token_usage(stdout=stdout, stderr=stderr, events=events).model_dump(),
            events=events,
            debug_artifacts=debug_artifacts,
        )
