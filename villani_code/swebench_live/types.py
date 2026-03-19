from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Platform = Literal["linux", "windows"]


@dataclass(frozen=True)
class AgentConfig:
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int = 3600
    install_timeout_seconds: int = 900
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunConfig:
    dataset: str
    split: str | None
    platform: Platform
    instance_limit: int | None
    output_path: Path
    logs_path: Path | None
    work_dir: Path
    agent: AgentConfig
    villani_source_dir: Path
    install_inside_container: bool = True


@dataclass(frozen=True)
class SwebenchLiveInstance:
    instance_id: str
    problem_statement: str
    docker_image: str | None = None
    raw_fields: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, object]) -> "SwebenchLiveInstance":
        instance_id = str(payload.get("instance_id") or "").strip()
        if not instance_id:
            raise ValueError("SWE-bench-Live instance is missing instance_id")
        problem_statement = str(payload.get("problem_statement") or payload.get("text") or "").strip()
        if not problem_statement:
            raise ValueError(f"SWE-bench-Live instance {instance_id} is missing problem_statement")
        docker_image = payload.get("docker_image")
        image_value = str(docker_image).strip() if isinstance(docker_image, str) else None
        return cls(
            instance_id=instance_id,
            problem_statement=problem_statement,
            docker_image=image_value or None,
            raw_fields=dict(payload),
        )


@dataclass(frozen=True)
class ProcessResult:
    command: list[str]
    sanitized_command: list[str]
    exit_code: int | None
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    timed_out: bool
    stdout: str
    stderr: str


@dataclass(frozen=True)
class AgentInvocationResult:
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    command: list[str]
    sanitized_command: list[str]
    error_summary: str | None = None


@dataclass(frozen=True)
class InstanceLogRecord:
    instance_id: str
    start_timestamp: str
    end_timestamp: str
    exit_code: int | None
    stdout_path: str
    stderr_path: str
    patch_byte_size: int
    duration_seconds: float
    error_summary: str | None = None
    timed_out: bool = False
