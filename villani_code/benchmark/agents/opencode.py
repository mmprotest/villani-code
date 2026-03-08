from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents.base import AgentRunner


class OpenCodeAgentRunner(AgentRunner):
    name = "opencode"

    def build_command(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
    ) -> list[str]:
        if not model:
            raise ValueError("opencode requires --model for fair same-model benchmarking")
        return ["opencode", "run", "--model", f"openai/{model}", "--prompt", prompt]

    def build_env(self, *, base_url: str | None, api_key: str | None) -> dict[str, str]:
        env = super().build_env(base_url=base_url, api_key=api_key)
        if base_url:
            env["OPENAI_API_BASE"] = base_url
        if api_key:
            env["OPENAI_API_KEY"] = api_key
        return env
