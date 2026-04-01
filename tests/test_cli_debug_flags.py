from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from villani_code.cli import app


class DummyRunner:
    def run(self, _instruction: str):
        return {"response": {"content": [{"type": "text", "text": "ok"}]}}


def test_cli_run_accepts_debug_flags(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_build_runner(*args, **kwargs):
        captured.update(kwargs)
        return DummyRunner()

    monkeypatch.setattr("villani_code.cli._build_runner", fake_build_runner)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "do thing",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
            "--debug",
            "trace",
            "--debug-dir",
            str(tmp_path / "debug"),
        ],
    )
    assert result.exit_code == 0
    assert str(captured.get("debug_mode")) == "trace"
    assert captured.get("debug_dir") == tmp_path / "debug"
