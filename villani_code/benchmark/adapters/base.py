from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from villani_code.benchmark.models import BenchmarkTask


@dataclass(slots=True)
class AgentAdapterConfig:
    agent_name: str
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int = 300
    unsafe: bool = False
    max_tokens: int | None = None
    thinking: str | None = None
    stream: bool = False
    provider: str | None = None
    extra_args: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    check_type: str
    success: bool
    details: str
    exit_code: int | None = None
    check_index: int = 0


@dataclass(slots=True)
class AgentRunResult:
    agent_name: str
    task_id: str
    success: bool
    exit_reason: str
    elapsed_seconds: float
    stdout: str
    stderr: str
    changed_files: list[str]
    git_diff: str
    validation_results: list[ValidationResult]
    catastrophic_failure: bool
    tokens_input: int | None
    tokens_output: int | None
    cost_usd: float | None
    raw_artifact_dir: str
    skipped: bool
    skip_reason: str | None
    retry_count: int = 0


class AgentAdapter(ABC):
    def __init__(self, config: AgentAdapterConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.agent_name

    @abstractmethod
    def run_task(self, task: BenchmarkTask, workspace_repo: Path, artifact_dir: Path) -> AgentRunResult:
        raise NotImplementedError
