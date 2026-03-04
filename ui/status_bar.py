from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic


@dataclass(slots=True)
class StatusSnapshot:
    connected: bool = False
    last_heartbeat: datetime | None = None
    total_tokens: int = 0
    tokens_last_minute: int = 0
    active_tools: int = 0
    last_tool_name: str = "-"
    settings_hint: str = "Ctrl+P"


@dataclass(slots=True)
class DebouncedRefresher:
    interval_seconds: float = 0.2
    _last_refresh: float = field(default=0.0, init=False)

    def should_refresh(self) -> bool:
        now = monotonic()
        if now - self._last_refresh >= self.interval_seconds:
            self._last_refresh = now
            return True
        return False


class StatusBar:
    def __init__(self, snapshot: StatusSnapshot | None = None, refresh_interval: float = 0.2) -> None:
        self.snapshot = snapshot or StatusSnapshot()
        self.refresher = DebouncedRefresher(interval_seconds=refresh_interval)

    def update(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            if hasattr(self.snapshot, key):
                setattr(self.snapshot, key, value)

    def format_fragments(self, width: int) -> list[tuple[str, str]]:
        return [("class:bottom-toolbar", self.format(width))]

    def format(self, width: int) -> str:
        heartbeat = self.snapshot.last_heartbeat
        heart_age = "-"
        if heartbeat:
            heart_age = f"{int((datetime.now(timezone.utc) - heartbeat).total_seconds())}s"
        net = "connected" if self.snapshot.connected else "disconnected"
        segments = [
            f"net:{net}/{heart_age}",
            f"tok:{self.snapshot.total_tokens} ({self.snapshot.tokens_last_minute}/m)",
            f"tools:{self.snapshot.active_tools}:{self.snapshot.last_tool_name}",
            f"settings:{self.snapshot.settings_hint}",
        ]
        return self._fit_segments(segments, width)

    def _fit_segments(self, segments: list[str], width: int) -> str:
        if width <= 0:
            return ""
        sep = " | "
        rendered = sep.join(segments)
        if len(rendered) <= width:
            return rendered
        trimmed: list[str] = []
        for segment in segments:
            if len(segment) > 18:
                trimmed.append(segment[:15] + "...")
            else:
                trimmed.append(segment)
        rendered = sep.join(trimmed)
        if len(rendered) <= width:
            return rendered
        return rendered[: max(0, width - 3)] + ("..." if width >= 3 else "")
