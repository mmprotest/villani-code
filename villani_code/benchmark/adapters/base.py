from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from villani_code.benchmark.models import TelemetryQuality


class AdapterEvent(BaseModel):
    type: str
    timestamp: float
    payload: dict[str, object] = {}


class AdapterRunConfig(BaseModel):
    prompt: str
    workspace_repo: Path
    timeout_seconds: int
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class AdapterRunResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    timeout: bool
    runtime_seconds: float
    telemetry_quality: TelemetryQuality
    events: list[AdapterEvent] = []


class AgentAdapter(ABC):
    name: str
    version = "1"

    @abstractmethod
    def build_command(self, config: AdapterRunConfig) -> list[str]: ...

    def run(self, config: AdapterRunConfig) -> AdapterRunResult:
        started = time.monotonic()
        cmd = self.build_command(config)
        events = [AdapterEvent(type="command_started", timestamp=time.monotonic(), payload={"command": " ".join(cmd)})]
        proc = subprocess.Popen(cmd, cwd=config.workspace_repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
        try:
            stdout, stderr = proc.communicate(timeout=config.timeout_seconds)
            timeout = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timeout = True
        events.append(AdapterEvent(type="command_finished", timestamp=time.monotonic(), payload={"exit_code": proc.returncode if not timeout else None}))
        return AdapterRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if not timeout else None,
            timeout=timeout,
            runtime_seconds=time.monotonic() - started,
            telemetry_quality=TelemetryQuality.INFERRED,
            events=events,
        )


class VillaniAdapter(AgentAdapter):
    name = "villani"

    def build_command(self, config: AdapterRunConfig) -> list[str]:
        if not config.model or not config.base_url:
            raise ValueError("villani requires model and base_url")
        command = [
            sys.executable,
            "-m",
            "villani_code.cli",
            "run",
            config.prompt,
            "--repo",
            str(config.workspace_repo),
            "--provider",
            "anthropic",
            "--model",
            config.model,
            "--base-url",
            config.base_url,
            "--no-stream",
        ]
        if config.api_key:
            command.extend(["--api-key", config.api_key])
        return command


class TemplateCliAdapter(AgentAdapter):
    template: list[str]

    def build_command(self, config: AdapterRunConfig) -> list[str]:
        command = list(self.template)
        command.append(config.prompt)
        return command


class ClaudeCodeAdapter(TemplateCliAdapter):
    name = "claude"
    template = ["claude", "-p"]


class OpenCodeAdapter(TemplateCliAdapter):
    name = "opencode"
    template = ["opencode", "run", "--prompt"]


class CopilotCliAdapter(TemplateCliAdapter):
    name = "copilot-cli"
    template = ["copilot", "suggest"]


class CommandAdapter(AgentAdapter):
    name = "cmd"

    def __init__(self, command: str) -> None:
        self.command = command

    def build_command(self, config: AdapterRunConfig) -> list[str]:
        return shlex.split(self.command) + [config.prompt]
