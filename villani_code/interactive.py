from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.tui.app import VillaniTUI
from villani_code.tui.assets import LAUNCH_BANNER


class InteractiveShell:
    LAUNCH_BANNER = LAUNCH_BANNER

    def __init__(self, runner: Any, repo: Path):
        self.runner = runner
        self.repo = repo

    def run(self) -> None:
        VillaniTUI(self.runner, self.repo).run()
