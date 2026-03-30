from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.optional_tui import remap_textual_import_error


def _load_tui_assets() -> str:
    try:
        from villani_code.tui.assets import LAUNCH_BANNER
    except ModuleNotFoundError as exc:
        remap_textual_import_error(exc)
    return LAUNCH_BANNER


def _load_tui_app() -> type[Any]:
    try:
        from villani_code.tui.app import VillaniTUI
    except ModuleNotFoundError as exc:
        remap_textual_import_error(exc)
    return VillaniTUI


class InteractiveShell:
    LAUNCH_BANNER = "villani-fying your terminal..."

    def __init__(self, runner: Any, repo: Path, villani_mode: bool = False, villani_objective: str | None = None):
        self.runner = runner
        self.repo = repo
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective
        self.LAUNCH_BANNER = f"villani-fying your terminal...\n{_load_tui_assets()}"

    def run(self) -> None:
        tui_app = _load_tui_app()
        tui_app(
            self.runner,
            self.repo,
            villani_mode=self.villani_mode,
            villani_objective=self.villani_objective,
        ).run()
