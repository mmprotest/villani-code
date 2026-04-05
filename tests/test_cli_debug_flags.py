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


def test_cli_trace_rebuild_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        '{"event_id":1,"run_id":"r1","ts":"2026-04-01T00:00:00+00:00","event_type":"tool_call_started","payload":{"tool_name":"Bash","tool_call_id":"t1"}}\n'
        '{"event_id":2,"run_id":"r1","ts":"2026-04-01T00:00:01+00:00","event_type":"tool_call_completed","payload":{"tool_name":"Bash","tool_call_id":"t1","exit_code":0}}\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["trace", "rebuild-summary", "--run-dir", str(run_dir)])
    assert result.exit_code == 0
    summary = (run_dir / "summary.json").read_text(encoding="utf-8")
    assert '"total_tool_calls": 1' in summary
