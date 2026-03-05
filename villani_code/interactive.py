from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.tui.app import VillaniTUI
from villani_code.tui.assets import LAUNCH_BANNER


class InteractiveShell:
    LAUNCH_BANNER = LAUNCH_BANNER

    def __init__(self, runner: Any, repo: Path, villani_mode: bool = False, villani_objective: str | None = None):
        self.runner = runner
        self.repo = repo
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective

    def run(self) -> None:
        VillaniTUI(self.runner, self.repo, villani_mode=self.villani_mode, villani_objective=self.villani_objective).run()
