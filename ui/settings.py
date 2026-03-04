from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class UserSettings:
    theme: str = "default"
    default_flags: dict[str, Any] | None = None
    shortcut_toggles: dict[str, bool] | None = None
    verbose: bool = False
    auto_accept_edits: bool = False
    pin_user_theme: bool = False
    max_prompt_chars: int = 200000

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserSettings":
        return cls(
            theme=payload.get("theme", "default"),
            default_flags=payload.get("default_flags") or {},
            shortcut_toggles=payload.get("shortcut_toggles") or {},
            verbose=bool(payload.get("verbose", False)),
            auto_accept_edits=bool(payload.get("auto_accept_edits", False)),
            pin_user_theme=bool(payload.get("pin_user_theme", False)),
            max_prompt_chars=int(payload.get("max_prompt_chars", 200000)),
        )


class SettingsManager:
    def __init__(self, repo: Path, home: Path | None = None) -> None:
        self.repo = repo
        self.home = home or Path.home()
        self.user_path = self.home / ".villani" / "settings.json"
        self.project_path = self.repo / ".villani" / "settings.json"
        self._last_signature: tuple[float, float, int, int] = (0.0, 0.0, 0, 0)
        self._cached = UserSettings()

    def load(self) -> UserSettings:
        user = self._read_file(self.user_path)
        project = self._read_file(self.project_path)
        merged = {**user, **project}
        if user.get("pin_user_theme"):
            merged["theme"] = user.get("theme", "default")
            merged["pin_user_theme"] = True
        self._cached = UserSettings.from_dict(merged)
        self._last_signature = self._signature()
        return self._cached

    def has_changes(self) -> bool:
        return self._signature() != self._last_signature

    def reload_if_changed(self) -> UserSettings | None:
        if self.has_changes():
            return self.load()
        return None

    def export_profile(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self._cached), indent=2), encoding="utf-8")

    def import_profile(self, path: Path, scope: str = "user") -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        target = self.user_path if scope == "user" else self.project_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.load()

    def _read_file(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _mtime(self, path: Path) -> float:
        return path.stat().st_mtime if path.exists() else 0.0

    def _signature(self) -> tuple[float, float, int, int]:
        return (
            self._mtime(self.user_path),
            self._mtime(self.project_path),
            self._size(self.user_path),
            self._size(self.project_path),
        )

    def _size(self, path: Path) -> int:
        return path.stat().st_size if path.exists() else 0
