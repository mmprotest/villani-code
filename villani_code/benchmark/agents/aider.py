from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents.base import AgentRunner


class AiderAgentRunner(AgentRunner):
    name = "aider"

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
            raise ValueError("aider requires --model for fair same-model benchmarking")
        if not base_url:
            raise ValueError("aider requires --base-url for fair same-model benchmarking")
        if not api_key:
            raise ValueError("aider requires --api-key for fair same-model benchmarking")
        return [
            "aider",
            "--yes",
            "--model",
            f"openai/{model}",
            "--openai-api-base",
            base_url,
            "--openai-api-key",
            api_key,
            "--message",
            prompt,
        ]
