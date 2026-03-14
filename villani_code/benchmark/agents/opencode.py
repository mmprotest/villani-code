from __future__ import annotations

import os
import shutil
from pathlib import Path

from villani_code.benchmark.agents.base import AgentRunner


class OpenCodeAgentRunner(AgentRunner):
    name = "opencode"

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
        if not model:
            raise ValueError("opencode requires --model for fair same-model benchmarking")
        executable = self._resolve_executable_name()
        return [executable, "run", "--model", model, "--hostname", base_url, "--command", prompt]

    def build_env(self, *, base_url: str | None, api_key: str | None) -> dict[str, str]:
        env = super().build_env(base_url=base_url, api_key=api_key)
        if base_url:
            env["OPENAI_API_BASE"] = base_url
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        return env
