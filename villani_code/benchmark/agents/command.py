from __future__ import annotations

import shlex
from pathlib import Path

from villani_code.benchmark.agents.base import AgentRunner
from villani_code.benchmark.models import FairnessClassification


class CommandAgentRunner(AgentRunner):
    name = "cmd"
    fairness_classification = FairnessClassification.NOT_COMPARABLE
    fairness_notes = "Arbitrary shell command adapter for smoke tests/debugging; not a fair agent comparison target."
    supports_model_override = False

    def __init__(self, command: str) -> None:
        self.command = command

    def build_command(
        self,
        repo_path: Path,
        prompt: str,
        model: str | None,
        base_url: str | None,
        api_key: str | None,
        provider: str | None,
    ) -> list[str]:
        return shlex.split(self.command) + [prompt]
