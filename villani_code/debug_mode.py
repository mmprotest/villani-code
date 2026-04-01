from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DebugMode(StrEnum):
    OFF = "off"
    NORMAL = "normal"
    TRACE = "trace"


@dataclass(slots=True)
class DebugConfig:
    mode: DebugMode = DebugMode.OFF
    debug_root: Path | None = None
    run_id: str | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != DebugMode.OFF

    @property
    def capture_model_io(self) -> bool:
        return self.mode == DebugMode.TRACE

    @property
    def capture_full_tool_payloads(self) -> bool:
        return self.mode == DebugMode.TRACE

    @property
    def capture_command_output(self) -> bool:
        return self.mode == DebugMode.TRACE

    @property
    def capture_mission_snapshots(self) -> bool:
        return self.mode != DebugMode.OFF


_DEBUG_TRUE_VALUES = {"1", "true", "yes", "on", "normal"}


def parse_debug_mode(value: str | None) -> DebugMode:
    if value is None:
        return DebugMode.OFF
    normalized = str(value).strip().lower()
    if not normalized:
        return DebugMode.OFF
    if normalized in _DEBUG_TRUE_VALUES:
        return DebugMode.NORMAL
    if normalized in {"off", "false", "0", "no"}:
        return DebugMode.OFF
    if normalized == "trace":
        return DebugMode.TRACE
    if normalized == "normal":
        return DebugMode.NORMAL
    raise ValueError(f"Unsupported debug mode: {value}")


def build_debug_config(debug: str | bool | None = None, debug_dir: str | Path | None = None) -> DebugConfig:
    if isinstance(debug, bool):
        mode = DebugMode.NORMAL if debug else DebugMode.OFF
    else:
        mode = parse_debug_mode(debug)
    root = Path(debug_dir).expanduser().resolve() if debug_dir else None
    return DebugConfig(mode=mode, debug_root=root)
