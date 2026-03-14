from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality


class AdapterEvent(BaseModel):
    type: str
    timestamp: float
    payload: dict[str, object] = Field(default_factory=dict)


class AdapterRunConfig(BaseModel):
    prompt: str
    workspace_repo: Path
    timeout_seconds: int
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    provider: str | None = None


class AdapterRunResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    timeout: bool
    runtime_seconds: float
    telemetry_quality: TelemetryQuality
    telemetry_field_quality_map: dict[str, FieldQuality] = Field(default_factory=dict)
    events: list[AdapterEvent] = Field(default_factory=list)
    debug_artifacts: dict[str, str] = Field(default_factory=dict)


class AgentAdapter:
    """Deprecated compatibility shim around benchmark agents."""

    name: str
    fairness_classification: FairnessClassification = FairnessClassification.COARSE_WRAPPER_ONLY
    fairness_notes = "Shared benchmark contract and harness-only scoring are used, but this adapter remains a coarse CLI wrapper with limited telemetry."


class VillaniAdapter(AgentAdapter):
    name = "villani"
    fairness_classification = FairnessClassification.APPROXIMATELY_COMPARABLE
    fairness_notes = "Shared benchmark contract and harness-only scoring improve comparability, but telemetry richness still differs across adapters."


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude"


class OpenCodeAdapter(AgentAdapter):
    name = "opencode"


class CopilotCliAdapter(AgentAdapter):
    name = "copilot-cli"


class CommandAdapter(AgentAdapter):
    name = "cmd"
    fairness_classification = FairnessClassification.NOT_COMPARABLE
    fairness_notes = "Arbitrary shell command adapter for smoke tests/debugging; not a fair agent comparison target."

    def __init__(self, command: str) -> None:
        self.command = command
