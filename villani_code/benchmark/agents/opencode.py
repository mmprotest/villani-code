from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from villani_code.benchmark.adapters.base import AdapterRunResult
from villani_code.benchmark.agents.base import AgentRunner


class OpenCodeAgentRunner(AgentRunner):
    name = "opencode"

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
        # Important: benchmark base_url/provider/model are model-provider settings.
        # OpenCode `--attach` is for connecting to an existing OpenCode session server.
        # Do not map provider/base_url/model benchmark fields onto OpenCode attach/session flags.
        unsupported: list[str] = []
        if provider:
            unsupported.append("provider selection")
        if base_url:
            unsupported.append("base_url passthrough")
        if model:
            unsupported.append("model configuration")
        if unsupported:
            unsupported_str = ", ".join(unsupported)
            raise ValueError(
                "OpenCode benchmark adapter does not support CLI passthrough for "
                f"{unsupported_str}. Configure OpenCode provider/model outside the benchmark "
                "and run without --provider/--base-url/--model overrides."
            )
        executable = self._resolve_executable_name()
        command = [executable, "run", "--dir", str(repo_path)]
        command.append(prompt)
        return command

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
        executable = self._resolve_executable_name()
        self._validate_cli_startup(executable, repo_path)
        result = super().run_agent(
            repo_path,
            prompt,
            model,
            base_url,
            api_key,
            provider,
            timeout,
            benchmark_config_json=benchmark_config_json,
            debug_dir=debug_dir,
        )
        startup_hint = self._startup_failure_message(stderr=result.stderr, stdout=result.stdout)
        if not result.timeout and result.exit_code not in {None, 0} and startup_hint:
            stderr = f"{startup_hint}\n\n{result.stderr}" if result.stderr else startup_hint
            result = result.model_copy(update={"stderr": stderr})
        return result
