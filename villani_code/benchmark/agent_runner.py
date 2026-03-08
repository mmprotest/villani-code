from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AgentExecution:
    stdout: str
    stderr: str
    exit_code: int | None
    timeout: bool
    runtime_seconds: float


class AgentRunner:
    def build_command(self, agent: str, prompt: str, repo: Path, model: str | None, base_url: str | None, api_key: str | None) -> list[str]:
        if agent == "villani":
            if not model or not base_url:
                raise ValueError("villani agent requires --model and --base-url")
            command = [
                sys.executable,
                "-m",
                "villani_code.cli",
                "run",
                prompt,
                "--repo",
                str(repo),
                "--provider",
                "anthropic",
                "--model",
                model,
                "--base-url",
                base_url,
                "--no-stream",
            ]
            if api_key:
                command.extend(["--api-key", api_key])
            return command

        template = {
            "claude": ["claude", "-p", prompt],
            "opencode": ["opencode", "run", "--prompt", prompt],
            "copilot-cli": ["copilot", "suggest", prompt],
        }
        if agent in template:
            return template[agent]
        if agent.startswith("cmd:"):
            return shlex.split(agent.removeprefix("cmd:")) + [prompt]
        raise ValueError(f"Unsupported agent '{agent}'")

    def run(self, agent: str, prompt: str, repo: Path, timeout_seconds: int, model: str | None, base_url: str | None, api_key: str | None) -> AgentExecution:
        command = self.build_command(agent, prompt, repo, model, base_url, api_key)
        started = time.monotonic()
        env = os.environ.copy()
        proc = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
            timeout = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timeout = True
        runtime = time.monotonic() - started
        return AgentExecution(stdout=stdout, stderr=stderr, exit_code=proc.returncode if not timeout else None, timeout=timeout, runtime_seconds=runtime)
