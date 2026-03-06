from __future__ import annotations

import subprocess
from dataclasses import dataclass

from rich.console import Console


@dataclass(slots=True)
class BenchmarkLogger:
    enabled: bool = True
    prefix: str = "[benchmark]"
    console: Console | None = None

    def __post_init__(self) -> None:
        if self.console is None:
            self.console = Console(stderr=True)

    def info(self, message: str) -> None:
        if self.enabled:
            self.console.print(f"{self.prefix} {message}", markup=False)

    def warn(self, message: str) -> None:
        if self.enabled:
            self.console.print(f"{self.prefix} WARNING: {message}", markup=False)

    def error(self, message: str) -> None:
        if self.enabled:
            self.console.print(f"{self.prefix} ERROR: {message}", markup=False)

    def task_label(self, index: int, total: int, task_id: str) -> str:
        return f"Task {index}/{total}: {task_id}"

    def agent_label(self, index: int, total: int, agent_name: str) -> str:
        return f"Agent {index}/{total}: {agent_name}"

    def agent_output(self, agent_name: str, stream_name: str, line: str) -> None:
        if self.enabled:
            normalized = line.rstrip("\r\n")
            self.console.print(f"{self.prefix} [{agent_name} {stream_name}] {normalized}", markup=False)


def render_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)
