from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from villani_code.benchmark.agents import build_agent_runner


@dataclass(slots=True)
class AgentExecution:
    stdout: str
    stderr: str
    exit_code: int | None
    timeout: bool
    runtime_seconds: float


class AgentRunner:
    """Deprecated compatibility shim for older imports.

    Benchmark execution is implemented by villani_code.benchmark.agents.
    """

    def run(
        self,
        agent: str,
        prompt: str,
        repo: Path,
        timeout_seconds: int,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None = None,
    ) -> AgentExecution:
        runner = build_agent_runner(agent)
        result = runner.run_agent(
            repo_path=repo,
            prompt=prompt,
            model=model,
            base_url=base_url,
            api_key=api_key,
            provider=provider,
            timeout=timeout_seconds,
        )
        return AgentExecution(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timeout=result.timeout,
            runtime_seconds=result.runtime_seconds,
        )
