from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
from villani_code.benchmark.agents.base import AgentRunner
from villani_code.benchmark.models import TelemetryQuality


class OpenCodeAgentRunner(AgentRunner):
    name = "opencode"

    PROMPT_ARTIFACT_FILENAME = "opencode_prompt.txt"
    INVOCATION_META_FILENAME = "opencode_invocation_meta.json"

    def __init__(self) -> None:
        self._cli_validated = False

    def _resolve_executable_name(self, *, is_windows: bool | None = None) -> str:
        if is_windows is None:
            is_windows = os.name == "nt"

        executable = "opencode.cmd" if is_windows else "opencode"
        if shutil.which(executable) is None:
            raise FileNotFoundError(
                f"OpenCode executable not found: '{executable}'. Install opencode and ensure it is on PATH."
            )
        return executable

    @staticmethod
    def _validate_backend_settings(
        *,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
    ) -> None:
        # Important: benchmark provider/base_url/model/api_key are backend settings.
        # OpenCode session flags are not equivalent and must not be used as substitutes.
        unsupported: list[str] = []
        if provider:
            unsupported.append("provider selection")
        if base_url:
            unsupported.append("base_url passthrough")
        if model:
            unsupported.append("model configuration")
        if api_key:
            unsupported.append("api_key passthrough")
        if unsupported:
            unsupported_str = ", ".join(unsupported)
            raise ValueError(
                "OpenCode benchmark adapter cannot prove backend equivalence for "
                f"{unsupported_str}. Refusing to run to prevent invalid benchmark comparisons. "
                "Use OpenCode without benchmark backend overrides, or add explicit OpenCode "
                "backend wiring first."
            )

    @staticmethod
    def _extract_reported_model(stdout: str, stderr: str) -> str | None:
        combined = f"{stdout}\n{stderr}"
        for line in combined.splitlines():
            if "model" not in line.lower():
                continue
            match = re.search(r"(?:active\s+)?model\s*[:=]\s*([^,;\]\)]+)", line, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

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
        self._validate_backend_settings(model=model, base_url=base_url, api_key=api_key, provider=provider)
        executable = self._resolve_executable_name()
        # Robust prompt delivery: send prompt over stdin to avoid multiline argv truncation,
        # especially when invoking opencode.cmd on Windows.
        return [executable, "run", "--dir", str(repo_path)]

    def build_env(self, *, base_url: str | None, api_key: str | None) -> dict[str, str]:
        return super().build_env(base_url=base_url, api_key=api_key)

    def _validate_cli_startup(self, executable: str, repo_path: Path) -> None:
        if self._cli_validated:
            return
        validation = subprocess.run(
            [executable, "run", "--help"],
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        combined = f"{validation.stdout}\n{validation.stderr}".lower()
        if validation.returncode != 0 or "session not found" in combined:
            raise RuntimeError(
                "OpenCode CLI startup validation failed for `opencode run --help`. "
                "The local OpenCode installation/CLI contract appears broken; verify `opencode run --help` works before benchmark runs."
            )
        self._cli_validated = True

    @staticmethod
    def _startup_failure_message(*, stderr: str, stdout: str) -> str | None:
        combined = f"{stderr}\n{stdout}".lower()
        if "session not found" in combined:
            return "OpenCode startup/config failure: `Session not found`. `--attach` expects an OpenCode session endpoint, not an LLM provider base URL."
        if "usage:" in combined or "opencode run" in combined and "--help" in combined:
            return "OpenCode startup/config failure: CLI usage/help output was returned instead of running the benchmark prompt."
        if "error" in combined and any(token in combined for token in ("config", "provider", "model", "startup")):
            return "OpenCode startup/config failure: OpenCode reported a startup or configuration error."
        return None

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
        self._validate_backend_settings(model=model, base_url=base_url, api_key=api_key, provider=provider)
        executable = self._resolve_executable_name()
        self._validate_cli_startup(executable, repo_path)

        started = time.monotonic()
        launch_prompt = self.render_launch_prompt(prompt)
        command = self.build_command(
            repo_path,
            launch_prompt,
            model,
            base_url,
            api_key,
            provider,
            benchmark_config_json=benchmark_config_json,
        )
        env = self.build_env(base_url=base_url, api_key=api_key)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(command)})]

        proc = subprocess.Popen(
            command,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(input=launch_prompt, timeout=timeout)
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

            prompt_path = debug_dir / self.PROMPT_ARTIFACT_FILENAME
            prompt_path.write_text(launch_prompt, encoding="utf-8")
            invocation_path = debug_dir / self.INVOCATION_META_FILENAME
            selected_env = {
                key: ("[REDACTED]" if self._is_sensitive_env_key(key) else value)
                for key, value in env.items()
                if key.startswith(("ANTHROPIC_", "OPENAI_", "VILLANI_", "AIDER_", "CODEX_", "OPENCODE_"))
            }
            invocation_path.write_text(
                json.dumps(
                    {
                        "delivery_mode": "stdin",
                        "executable": command[0],
                        "argv": command,
                        "prompt_artifact": str(prompt_path),
                        "env": selected_env,
                        "reported_active_model": self._extract_reported_model(stdout, stderr),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            debug_artifacts["opencode_prompt"] = str(prompt_path)
            debug_artifacts["opencode_invocation_meta"] = str(invocation_path)

        result = AdapterRunResult(
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
        startup_hint = self._startup_failure_message(stderr=result.stderr, stdout=result.stdout)
        if not result.timeout and result.exit_code not in {None, 0} and startup_hint:
            stderr = f"{startup_hint}\n\n{result.stderr}" if result.stderr else startup_hint
            result = result.model_copy(update={"stderr": stderr})
        return result
