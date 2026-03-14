from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents.base import AgentRunner


class ClaudeCodeAgentRunner(AgentRunner):
    name = "claude-code"

    CLI_EXECUTABLE = "claude"
    NON_INTERACTIVE_FLAGS = ["--print", "--output-format", "json"]
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
        # Benchmarks run headless in ephemeral workspaces, so we use print-mode + permissive permissions
        # to avoid interactive prompts while still allowing edits and shell execution.
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
