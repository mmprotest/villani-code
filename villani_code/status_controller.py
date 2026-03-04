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
    def __init__(self, fps: float = 10.0, recent_max: int = 4, render_to_stdout: bool = True) -> None:
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
        self._render_to_stdout = render_to_stdout

    def _build_themes(self) -> list[SpinnerTheme]:
        slogans = [
            "Villanifying the repo",
            "Villanification underway",
            "Villanivision scanning files",
            "Villaniscope focusing",
            "Villanidering through code",
            "Villaniguard checking edges",
            "Villanigniting the test suite",
            "Villanity check in progress",
            "Villanizer loading context",
            "Villaniation station running",
            "Villaniating patches",
            "Villanilogic compiling thoughts",
            "Villanivating a cleaner UX",
            "Villanimizing display jitter",
            "Villanifying status signals",
            "Villanispecting tool plans",
            "Villani chaos, deployed",
            "Adding Villani sauce",
            "Villani makeover time",
            "Cranking the Villani meter",
            "Summoning Villani energy",
            "Villani polish applied",
            "Letting Villani drive",
            "Villani scrolls consulted",
            "Villani vs spaghetti code",
            "Channeling Villani for CI",
            "Villani vision on",
            "Villani exorcism time",
            "Villani dust deployed",
            "Awaiting Villani guidance",
            "Villani patch incoming",
            "Villani certified vibes",
            "Paging Villani for merge",
            "Villani audit running",
            "Villani folk tale forming",
            "Villani mode enabled",
            "Villani duct tape fix",
            "Bribing with Villani praise",
            "Villani ritual engaged",
            "Villani car wash pass",
            "Villani refactor instincts",
            "Feeding Villani to tests",
            "Villani UX witchcraft",
            "Villani plan brewing",
            "Villani graph diplomacy",
            "Villani confetti warnings",
            "Found Villani, not sanity",
            "Villani lasso ready",
            "Villani logic engaged",
            "Villani resilient upgrade",
            "Villani flashlight logs",
            "Villani discipline time",
            "Villani time travel",
            "Villani TODO prophecy",
            "Villani order restored",
            "Villani optimism only",

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
            SpinnerTheme(["⟦   ⟧", "⟦v  ⟧", "⟦vv ⟧", "⟦vvv⟧", "⟦ vv⟧", "⟦  v⟧"], slogans, micros),
            SpinnerTheme(["⠁", "⠃", "⠇", "⠧", "⠷", "⠿", "⠾", "⠶", "⠦", "⠆", "⠂"], slogans, micros),
            SpinnerTheme(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"], slogans, micros),
            SpinnerTheme(["<      V>", "<     Vi>", "<    Vil>", "<   Vill>", "<  Villa>", "< Villan>", "<Villani>", "<illani >", "<llani  >", "<lani   >", "<ani    >", "<ni     >", "<i      >", "<       >"], slogans, micros),
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

    def stop_spinner(self, phase: str = "Responding", detail: str = "") -> None:
        with self._lock:
            self.current_phase = phase
            self.current_detail = detail
            self._spinning = False
        if phase:
            self._render()
        else:
            self._clear_line()

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
        if not self._render_to_stdout:
            return
        width = shutil.get_terminal_size((120, 20)).columns
        clipped = line if len(line) <= width else line[: max(0, width - 3)] + "..."
        sys.stdout.write("\r\033[2K" + clipped)
        sys.stdout.flush()

    def _clear_line(self) -> None:
        if not self._render_to_stdout:
            return
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()

    def status_line(self) -> str:
        with self._lock:
            detail = f" — {self.current_detail}" if self.current_detail else ""
            return f"{self.current_phase}{detail}"
