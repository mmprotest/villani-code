from __future__ import annotations

import importlib
import sys
from pathlib import Path

from typer.testing import CliRunner


runner = CliRunner()


def _block_textual(monkeypatch) -> None:
    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "textual" or name.startswith("textual."):
            raise ModuleNotFoundError("No module named 'textual'", name="textual")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)


def test_cli_app_importable_without_textual(monkeypatch) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    module = importlib.import_module("villani_code.cli")
    assert module.app is not None


def test_init_runs_without_textual(monkeypatch, tmp_path: Path) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    app = importlib.import_module("villani_code.cli").app
    result = runner.invoke(app, ["init", "--repo", str(tmp_path)])
    assert result.exit_code == 0


def test_run_runs_without_textual(monkeypatch, tmp_path: Path) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    module = importlib.import_module("villani_code.cli")

    class DummyRunner:
        def run(self, _instruction: str):
            return {"response": {"content": [{"type": "text", "text": "ok"}]}}

    monkeypatch.setattr(module, "_build_runner", lambda *a, **k: DummyRunner())
    result = runner.invoke(
        module.app,
        [
            "run",
            "do thing",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "ok" in result.stdout




def test_core_imports_do_not_pull_textual(monkeypatch) -> None:
    _block_textual(monkeypatch)
    for module_name in ["villani_code.cli", "villani_code.interactive", "villani_code.state", "textual"]:
        sys.modules.pop(module_name, None)
    importlib.import_module("villani_code.cli")
    importlib.import_module("villani_code.state")
    assert "textual" not in sys.modules


def test_default_launch_path_fails_cleanly_without_tui(monkeypatch, tmp_path: Path) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    module = importlib.import_module("villani_code.cli")
    monkeypatch.setattr(module, "_build_runner", lambda *a, **k: object())

    result = runner.invoke(
        module.app,
        [
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "requires the optional TUI dependencies" in result.output
def test_interactive_fails_cleanly_without_tui(monkeypatch, tmp_path: Path) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    module = importlib.import_module("villani_code.cli")
    monkeypatch.setattr(module, "_build_runner", lambda *a, **k: object())

    result = runner.invoke(
        module.app,
        [
            "interactive",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "requires the optional TUI dependencies" in result.output


def test_run_tolerates_malformed_response_content(monkeypatch, tmp_path: Path) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    module = importlib.import_module("villani_code.cli")

    class DummyRunner:
        def run(self, _instruction: str):
            return {"response": {"content": ["plain", 7, {"type": "text", "text": "ok"}, {"x": 1}]}}

    monkeypatch.setattr(module, "_build_runner", lambda *a, **k: DummyRunner())
    result = runner.invoke(
        module.app,
        [
            "run",
            "do thing",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "plain" in result.stdout
    assert "ok" in result.stdout


def test_run_tolerates_missing_response_shape(monkeypatch, tmp_path: Path) -> None:
    _block_textual(monkeypatch)
    sys.modules.pop("villani_code.cli", None)
    sys.modules.pop("villani_code.interactive", None)
    module = importlib.import_module("villani_code.cli")

    class DummyRunner:
        def run(self, _instruction: str):
            return {"response": None}

    monkeypatch.setattr(module, "_build_runner", lambda *a, **k: DummyRunner())
    result = runner.invoke(
        module.app,
        [
            "run",
            "do thing",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
