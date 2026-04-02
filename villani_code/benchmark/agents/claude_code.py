from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
from villani_code.benchmark.agents.base import AgentRunner
from villani_code.benchmark.agents.claude_hook_events import parse_hook_events_jsonl
from villani_code.benchmark.agents.cli_postprocess import apply_stdout_diff_if_needed
from villani_code.benchmark.agents.workspace_diff import diff_workspace, snapshot_workspace
from villani_code.benchmark.models import FieldQuality, TelemetryQuality


class ClaudeCodeAgentRunner(AgentRunner):
    name = "claude-code"

    CLI_EXECUTABLE = "claude"
    NON_INTERACTIVE_FLAGS = ["--bare", "--print", "--output-format", "json"]
    PERMISSION_FLAGS = ["--permission-mode", "bypassPermissions"]

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
        if not model:
            raise ValueError("claude-code requires --model for fair same-model benchmarking")
        return [
            self.CLI_EXECUTABLE,
            "--model",
            model,
            *self.NON_INTERACTIVE_FLAGS,
            *self.PERMISSION_FLAGS,
            prompt,
        ]

    def build_env(self, *, base_url: str | None, api_key: str | None) -> dict[str, str]:
        env = super().build_env(base_url=base_url, api_key=api_key)
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        return env

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
        base_command = self.build_command(
            repo_path,
            launch_prompt,
            model,
            base_url,
            api_key,
            provider,
            benchmark_config_json=benchmark_config_json,
        )
        env = self.build_env(base_url=base_url, api_key=api_key)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(base_command)})]

        temp_root = debug_dir if debug_dir is not None else Path(tempfile.mkdtemp(prefix="claude-bench-"))
        temp_root.mkdir(parents=True, exist_ok=True)
        hook_events_path = temp_root / "claude_hook_events.jsonl"
        settings_path = temp_root / "claude_hook_settings.json"
        settings_payload = self._hook_settings(hook_events_path)
        settings_path.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")

        command = [*base_command[:-1], "--settings", str(settings_path), base_command[-1]]
        snapshot_ignored = {str(settings_path.relative_to(repo_path)).replace("\\", "/")} if settings_path.is_relative_to(repo_path) else set()
        if hook_events_path.is_relative_to(repo_path):
            snapshot_ignored.add(str(hook_events_path.relative_to(repo_path)).replace("\\", "/"))
        baseline = snapshot_workspace(repo_path, extra_ignored=snapshot_ignored)

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
            debug_artifacts["claude_hook_settings"] = str(settings_path)
            debug_artifacts["claude_hook_events"] = str(hook_events_path)
            debug_artifacts["claude_hook_logger"] = str(Path(__file__).with_name("claude_hook_logger.py"))

        hook_result = parse_hook_events_jsonl(hook_events_path)
        events.extend(hook_result.events)

        hook_paths = {
            str(event.payload.get("path"))
            for event in hook_result.events
            if isinstance(event.payload.get("path"), str) and event.payload.get("path")
        }
        workspace_changes = diff_workspace(baseline, repo_path, extra_ignored=snapshot_ignored)
        synthesized_events = self._events_from_workspace_changes(workspace_changes, hook_paths)
        events.extend(synthesized_events)

        stdout_touched: list[str] = []
        stdout_diagnostics: dict[str, Any] | None = None
        if not workspace_changes.changed_files and not hook_paths:
            stdout_touched, stdout_diagnostics = apply_stdout_diff_if_needed(repo_path, stdout)
            if stdout_touched:
                events.append(
                    AdapterEvent(
                        type="apply_patch",
                        timestamp=time.monotonic(),
                        payload={"type": "apply_patch", "source": "stdout_diff", "paths": stdout_touched},
                    )
                )
                for path in stdout_touched:
                    events.append(
                        AdapterEvent(
                            type="write_file",
                            timestamp=time.monotonic(),
                            payload={"type": "write_file", "source": "stdout_diff", "path": path, "file_path": path},
                        )
                    )

        if debug_dir is not None:
            (debug_dir / "claude_hook_summary.json").write_text(json.dumps(hook_result.summary, indent=2), encoding="utf-8")
            debug_artifacts["claude_hook_summary"] = str(debug_dir / "claude_hook_summary.json")
            ws_summary = {
                "created": workspace_changes.created,
                "modified": workspace_changes.modified,
                "deleted": workspace_changes.deleted,
                "synthesized_events": len(synthesized_events),
            }
            (debug_dir / "workspace_change_summary.json").write_text(json.dumps(ws_summary, indent=2), encoding="utf-8")
            debug_artifacts["workspace_change_summary"] = str(debug_dir / "workspace_change_summary.json")
            if stdout_diagnostics is not None:
                (debug_dir / "stdout_postprocess_summary.json").write_text(
                    json.dumps({**stdout_diagnostics, "touched": stdout_touched}, indent=2),
                    encoding="utf-8",
                )
                debug_artifacts["stdout_postprocess_summary"] = str(debug_dir / "stdout_postprocess_summary.json")

        field_quality = self._field_quality()
        has_hooks = bool(hook_result.events)
        has_workspace = bool(workspace_changes.changed_files)
        field_quality["num_shell_commands"] = FieldQuality.EXACT if has_hooks else FieldQuality.INFERRED
        field_quality["num_failed_commands"] = FieldQuality.EXACT if has_hooks else FieldQuality.INFERRED
        field_quality["tool_calls_total"] = FieldQuality.EXACT if has_hooks else FieldQuality.INFERRED
        field_quality["time_to_first_edit"] = FieldQuality.INFERRED if has_hooks else FieldQuality.UNAVAILABLE
        if has_workspace or has_hooks:
            field_quality["touched_file_paths"] = FieldQuality.EXACT
            field_quality["file_writes"] = FieldQuality.INFERRED
            field_quality["patch_attempts"] = FieldQuality.INFERRED

        return AdapterRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timeout=timeout_hit,
            runtime_seconds=runtime_seconds,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map=field_quality,
            events=events,
            debug_artifacts=debug_artifacts,
        )

    @staticmethod
    def _hook_settings(output_path: Path) -> dict[str, Any]:
        logger = Path(__file__).with_name("claude_hook_logger.py")
        command = [sys.executable, str(logger), str(output_path)]
        return {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": command}]},
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]},
                ],
                "PostToolUseFailure": [
                    {"matcher": "Bash|Edit|Write", "hooks": [{"type": "command", "command": command}]},
                ],
                "PermissionDenied": [
                    {"matcher": "Bash|Edit|Write", "hooks": [{"type": "command", "command": command}]},
                ],
            }
        }

    @staticmethod
    def _events_from_workspace_changes(changes: Any, hook_paths: set[str]) -> list[AdapterEvent]:
        events: list[AdapterEvent] = []
        changed = [path for path in changes.changed_files if path not in hook_paths]
        if not changed:
            return events
        ts = time.monotonic()
        events.append(AdapterEvent(type="apply_patch", timestamp=ts, payload={"type": "apply_patch", "source": "workspace_diff", "paths": changed}))
        for path in changed:
            events.append(
                AdapterEvent(
                    type="write_file",
                    timestamp=ts,
                    payload={"type": "write_file", "source": "workspace_diff", "path": path, "file_path": path},
                )
            )
        return events
