from __future__ import annotations

import random
import secrets
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class SpinnerTheme:
    frames: list[str]
    slogans: list[str]
    micros: list[str]


class StatusController:
    def __init__(self, fps: float = 10.0, recent_max: int = 4) -> None:
        self.current_phase = "Idle"
        self.current_detail = ""
        self.recent_actions: deque[str] = deque(maxlen=recent_max)
        self._fps = fps
        self._interval = 1.0 / max(1.0, fps)
        self._rng = random.Random(secrets.randbits(64) ^ time.time_ns())
        self._themes = self._build_themes()
        self._theme = self._themes[0]
        self._frame_index = 0
        self._active_slogan = ""
        self._active_micro = ""
        self._spinning = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _build_themes(self) -> list[SpinnerTheme]:
        slogans = [
            "Villanifying the repo",
            "Villani-fication underway",
            "Villani-vision scanning files",
            "Villani-scope focusing",
            "Villani-dering through code",
            "Villani-guard checking edges",
            "Villani-gniting the test suite",
            "Villani-ty check in progress",
            "Villani-zer loading context",
            "Villani-ation station running",
            "Villani-ating patches",
            "Villani-logic compiling thoughts",
            "Villani-vating a cleaner UX",
            "Villani-mizing display jitter",
            "Villani-fying status signals",
            "Villani-specting tool plans",
        ]
        micros = [
            "Villani-sniffing diffs",
            "Villani-wrangling imports",
            "Villani-juggling tokens",
            "Villani-tuning prompts",
            "Villani-peeking at README",
            "Villani-mapping file paths",
            "Villani-polishing terminal vibes",
        ]
        return [
            SpinnerTheme(["-", "\\", "|", "/"], slogans, micros),
            SpinnerTheme([".", "o", "O", "o"], slogans, micros),
            SpinnerTheme([">  ", ">> ", " >>", "  >", " < ", "<< ", " <<", "  <"], slogans, micros),
            SpinnerTheme(["[   ]", "[=  ]", "[== ]", "[===]", "[ ==]", "[  =]"], slogans, micros),
        ]

    def start_waiting(self, phase: str, detail: str = "") -> None:
        with self._lock:
            self._theme = self._rng.choice(self._themes)
            self._active_slogan = self._rng.choice(self._theme.slogans)
            self._active_micro = self._rng.choice(self._theme.micros)
            self.current_phase = self._active_slogan if phase == "Thinking" else phase
            self.current_detail = ""
            self._frame_index = 0
            self._spinning = True
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        self._render()

    def stop_spinner(self, phase: str = "Streaming", detail: str = "") -> None:
        with self._lock:
            self.current_phase = phase
            self.current_detail = detail
            self._spinning = False
        self._render()

    def suspend(self) -> None:
        with self._lock:
            self._spinning = False
        self._clear_line()

    def update_phase(self, phase: str, detail: str = "") -> None:
        with self._lock:
            self.current_phase = phase
            self.current_detail = detail
        self._render()

    def push_action(self, action: str) -> None:
        with self._lock:
            self.recent_actions.appendleft(action)
        self._render()

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.3)
        self._clear_line()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                spinning = self._spinning
            if spinning:
                self._render()
            time.sleep(self._interval)

    def _render(self) -> None:
        with self._lock:
            frame = self._theme.frames[self._frame_index % len(self._theme.frames)] if self._spinning else "*"
            if self._spinning:
                self._frame_index += 1
            line = f"[{frame}] {self.current_phase}"
        width = shutil.get_terminal_size((120, 20)).columns
        clipped = line if len(line) <= width else line[: max(0, width - 3)] + "..."
        sys.stdout.write("\r\033[2K" + clipped)
        sys.stdout.flush()

    def _clear_line(self) -> None:
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()
