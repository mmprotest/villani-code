from __future__ import annotations

import random
import secrets
import time

from textual.widgets import Static

from villani_code.tui.assets import SpinnerTheme, spinner_themes


class SpinnerWidget(Static):
    def __init__(self) -> None:
        super().__init__("[*] Idle", id="spinner")
        self._themes = spinner_themes()
        self._rng = random.Random(secrets.randbits(64) ^ time.time_ns())
        self._theme: SpinnerTheme = self._themes[0]
        self._frame = 0
        self._active = False
        self._label = "Idle"

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def set_state(self, active: bool, label: str | None = None) -> None:
        self._active = active
        if label:
            self._label = label
        if active:
            self._theme = self._rng.choice(self._themes)
            self._label = label or self._rng.choice(self._theme.slogans)
            self._frame = 0
        self._render_now()

    def _tick(self) -> None:
        if self._active:
            self._frame += 1
            self._render_now()

    def _render_now(self) -> None:
        if self._active:
            frame = self._theme.frames[self._frame % len(self._theme.frames)]
            self.update(f"[{frame}] {self._label}")
        else:
            self.update(f"[*] {self._label}")
