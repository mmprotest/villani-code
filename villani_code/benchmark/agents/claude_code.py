from __future__ import annotations

import json
import shlex
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
    DEFAULT_ALLOWED_TOOLS = "Read,Edit,Write,Bash"
    _CAPABILITY_FLAGS = {
        "bare": "--bare",
        "settings": "--settings",
        "include_hook_events": "--include-hook-events",
        "allowed_tools": "--allowedTools",
        "output_format": "--output-format",
    }
    _capability_cache: dict[str, bool] | None = None

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
        options = self._parse_options(benchmark_config_json)
        if options.get("claude_deep_debug"):
            return self._run_deep_debug_mode(
                repo_path=repo_path,
                prompt=prompt,
                model=model,
                base_url=base_url,
                api_key=api_key,
                provider=provider,
                timeout=timeout,
                benchmark_config_json=benchmark_config_json,
                debug_dir=debug_dir,
            )

        started = time.monotonic()
        launch_prompt = self.render_launch_prompt(prompt)
        capabilities = self.detect_cli_capabilities()
        base_command = self.build_command(
            repo_path,
            launch_prompt,
            model,
            base_url,
            api_key,
            provider,
            benchmark_config_json=benchmark_config_json,
        )
        base_command = self._apply_capabilities_to_command(base_command, capabilities, deep_debug=False)
        env = self.build_env(base_url=base_url, api_key=api_key)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(base_command)})]

        temp_root = debug_dir if debug_dir is not None else Path(tempfile.mkdtemp(prefix="claude-bench-"))
        temp_root.mkdir(parents=True, exist_ok=True)
        hook_events_path = temp_root / "claude_hook_events.jsonl"
        hook_errors_path = temp_root / "claude_hook_events.err"
        hook_breadcrumbs_path = temp_root / "claude_hook_events.breadcrumbs.log"
        settings_path = temp_root / "claude_hook_settings.json"
        settings_payload = self._hook_settings(hook_events_path, hook_errors_path, hook_breadcrumbs_path)
        settings_path.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")
        command = list(base_command)
        if capabilities.get("settings", False):
            command = [*command[:-1], "--settings", str(settings_path), command[-1]]
        snapshot_ignored = {str(settings_path.relative_to(repo_path)).replace("\\", "/")} if settings_path.is_relative_to(repo_path) else set()
        if hook_events_path.is_relative_to(repo_path):
            snapshot_ignored.add(str(hook_events_path.relative_to(repo_path)).replace("\\", "/"))
        if hook_errors_path.is_relative_to(repo_path):
            snapshot_ignored.add(str(hook_errors_path.relative_to(repo_path)).replace("\\", "/"))
        if hook_breadcrumbs_path.is_relative_to(repo_path):
            snapshot_ignored.add(str(hook_breadcrumbs_path.relative_to(repo_path)).replace("\\", "/"))
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
            debug_artifacts["claude_hook_errors"] = str(hook_errors_path)
            debug_artifacts["claude_hook_breadcrumbs"] = str(hook_breadcrumbs_path)
            debug_artifacts["claude_hook_logger"] = str(Path(__file__).with_name("claude_hook_logger.py"))
            capabilities_path = debug_dir / "claude_cli_capabilities.json"
            capabilities_path.write_text(json.dumps(capabilities, indent=2), encoding="utf-8")
            debug_artifacts["claude_cli_capabilities"] = str(capabilities_path)

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
            hook_summary = self._build_hook_summary(
                hook_result_summary=hook_result.summary,
                events_path=hook_events_path,
                errors_path=hook_errors_path,
                breadcrumbs_path=hook_breadcrumbs_path,
                settings_supported=capabilities.get("settings", False),
            )
            (debug_dir / "claude_hook_summary.json").write_text(json.dumps(hook_summary, indent=2), encoding="utf-8")
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
    def _hook_settings(output_path: Path, errors_path: Path, breadcrumbs_path: Path) -> dict[str, Any]:
        wrapper = Path(__file__).with_name("claude_hook_wrapper.py")
        command = ClaudeCodeAgentRunner._format_command(
            [sys.executable, str(wrapper), str(output_path), str(errors_path), str(breadcrumbs_path)]
        )
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
    def _format_command(parts: list[str], *, is_windows: bool | None = None) -> str:
        if is_windows is None:
            is_windows = sys.platform.startswith("win")
        if is_windows:
            return subprocess.list2cmdline(parts)
        return shlex.join(parts)

    @classmethod
    def detect_cli_capabilities(cls) -> dict[str, bool]:
        if cls._capability_cache is not None:
            return dict(cls._capability_cache)
        try:
            completed = subprocess.run(
                [cls.CLI_EXECUTABLE, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            help_text = f"{completed.stdout}\n{completed.stderr}"
        except (FileNotFoundError, subprocess.SubprocessError):
            help_text = ""
        cls._capability_cache = {name: (flag in help_text) for name, flag in cls._CAPABILITY_FLAGS.items()}
        return dict(cls._capability_cache)

    def _apply_capabilities_to_command(self, command: list[str], capabilities: dict[str, bool], *, deep_debug: bool) -> list[str]:
        updated = list(command)
        if not capabilities.get("bare", False):
            updated = [part for part in updated if part != "--bare"]
        if capabilities.get("output_format", False):
            format_value = "stream-json" if deep_debug else "json"
            if "--output-format" in updated:
                idx = updated.index("--output-format")
                if idx + 1 < len(updated):
                    updated[idx + 1] = format_value
            else:
                updated = [*updated[:-1], "--output-format", format_value, updated[-1]]
        else:
            updated = self._remove_flag_with_value(updated, "--output-format")
        if deep_debug and "--verbose" not in updated:
            updated = [*updated[:-1], "--verbose", updated[-1]]
        if deep_debug and capabilities.get("include_hook_events", False):
            updated = [*updated[:-1], "--include-hook-events", updated[-1]]
        if capabilities.get("allowed_tools", False):
            updated = self._remove_flag_with_value(updated, "--permission-mode")
            if "--allowedTools" not in updated:
                updated = [*updated[:-1], "--allowedTools", self.DEFAULT_ALLOWED_TOOLS, updated[-1]]
        return updated

    @staticmethod
    def _remove_flag_with_value(command: list[str], flag: str) -> list[str]:
        if flag not in command:
            return command
        idx = command.index(flag)
        end = idx + 2 if idx + 1 < len(command) else idx + 1
        return [part for i, part in enumerate(command) if i < idx or i >= end]

    @staticmethod
    def _parse_options(benchmark_config_json: str | None) -> dict[str, Any]:
        if not benchmark_config_json:
            return {}
        try:
            loaded = json.loads(benchmark_config_json)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _file_summary(path: Path, max_lines: int = 5) -> dict[str, Any]:
        if not path.exists():
            return {"exists": False, "line_count": 0, "snippet": []}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"exists": True, "line_count": len(lines), "snippet": lines[:max_lines]}

    def _build_hook_summary(
        self,
        *,
        hook_result_summary: dict[str, Any],
        events_path: Path,
        errors_path: Path,
        breadcrumbs_path: Path,
        settings_supported: bool,
    ) -> dict[str, Any]:
        return {
            "settings_supported": settings_supported,
            "hook_parse": hook_result_summary,
            "events": self._file_summary(events_path),
            "errors": self._file_summary(errors_path),
            "breadcrumbs": self._file_summary(breadcrumbs_path),
        }

    def _run_deep_debug_mode(
        self,
        *,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
        timeout: int,
        benchmark_config_json: str | None,
        debug_dir: Path | None,
    ) -> AdapterRunResult:
        started = time.monotonic()
        launch_prompt = self.render_launch_prompt(prompt)
        capabilities = self.detect_cli_capabilities()
        command = self.build_command(repo_path, launch_prompt, model, base_url, api_key, provider, benchmark_config_json=benchmark_config_json)
        command = self._apply_capabilities_to_command(command, capabilities, deep_debug=True)
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
            stream_path = debug_dir / "claude_stream_events.jsonl"
            stream_path.write_text(stdout, encoding="utf-8")
            caps_path = debug_dir / "claude_cli_capabilities.json"
            caps_path.write_text(json.dumps(capabilities, indent=2), encoding="utf-8")
            debug_artifacts["claude_stream_events"] = str(stream_path)
            debug_artifacts["claude_cli_capabilities"] = str(caps_path)
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
