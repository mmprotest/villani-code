import json
from pathlib import Path

from ui.settings import SettingsManager


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_settings_precedence_project_over_user(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    _write(home / ".villani" / "settings.json", {"theme": "default", "verbose": False})
    _write(repo / ".villani" / "settings.json", {"theme": "high-contrast", "verbose": True})
    mgr = SettingsManager(repo, home=home)
    settings = mgr.load()
    assert settings.theme == "high-contrast"
    assert settings.verbose is True


def test_settings_pin_user_theme(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    _write(home / ".villani" / "settings.json", {"theme": "default", "pin_user_theme": True})
    _write(repo / ".villani" / "settings.json", {"theme": "high-contrast"})
    mgr = SettingsManager(repo, home=home)
    settings = mgr.load()
    assert settings.theme == "default"


def test_hot_reload_detection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    path = home / ".villani" / "settings.json"
    _write(path, {"theme": "default"})
    mgr = SettingsManager(repo, home=home)
    mgr.load()
    _write(path, {"theme": "high-contrast"})
    assert mgr.reload_if_changed() is not None
