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
        '{"event_id":1,"run_id":"r1","ts":"2026-04-01T00:00:00+00:00","event_type":"run_started","payload":{"objective":"x"}}\n'
        '{"event_id":2,"run_id":"r1","ts":"2026-04-01T00:00:00+00:00","event_type":"tool_call_started","payload":{"tool_name":"Bash","tool_call_id":"t1"}}\n'
        '{"event_id":3,"run_id":"r1","ts":"2026-04-01T00:00:01+00:00","event_type":"tool_call_completed","payload":{"tool_name":"Bash","tool_call_id":"t1","exit_code":0}}\n'
        '{"event_id":4,"run_id":"r1","ts":"2026-04-01T00:00:02+00:00","event_type":"run_completed","payload":{"termination_reason":"completed"}}\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["trace", "rebuild-summary", "--run-dir", str(run_dir)])
    assert result.exit_code == 0
    summary = (run_dir / "summary.json").read_text(encoding="utf-8")
    assert '"total_tool_calls": 1' in summary


def test_cli_trace_rebuild_tool_calls(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        '{"event_id":1,"run_id":"r1","ts":"2026-04-01T00:00:00+00:00","event_type":"run_started","payload":{"objective":"x"}}\n'
        '{"event_id":2,"run_id":"r1","ts":"2026-04-01T00:00:00+00:00","event_type":"tool_call_started","payload":{"tool_name":"Read","tool_call_id":"t1","args":{"file_path":"a.py"}}}\n'
        '{"event_id":3,"run_id":"r1","ts":"2026-04-01T00:00:01+00:00","event_type":"tool_call_completed","payload":{"tool_name":"Read","tool_call_id":"t1","summary":"ok"}}\n'
        '{"event_id":4,"run_id":"r1","ts":"2026-04-01T00:00:02+00:00","event_type":"run_completed","payload":{"termination_reason":"completed"}}\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(app, ["trace", "rebuild-tool-calls", "--run-dir", str(run_dir)])
    assert result.exit_code == 0
    rows = (run_dir / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_cli_orchestrate_passes_run_flags(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_orchestrate(**kwargs):
        captured.update(kwargs)
        return {"stop_reason": "ok"}

    monkeypatch.setattr("villani_code.cli.orchestrate", fake_orchestrate)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "orchestrate",
            "fix task",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
            "--max-tokens",
            "2048",
            "--stream",
            "--thinking",
            '{"budget_tokens":128}',
            "--unsafe",
            "--verbose",
            "--extra-json",
            '{"x":1}',
            "--redact",
            "--dangerously-skip-permissions",
            "--auto-accept-edits",
            "--no-auto-approve",
            "--plan-mode",
            "strict",
            "--max-repair-attempts",
            "5",
            "--small-model",
            "--provider",
            "openai",
            "--api-key",
            "k",
            "--debug",
            "trace",
            "--debug-dir",
            str(tmp_path / "dbg"),
            "--workers",
            "4",
            "--scout-workers",
            "2",
            "--patch-workers",
            "2",
            "--rounds",
            "2",
            "--worker-timeout",
            "60",
            "--verify-command",
            "pytest -q",
            "--output-dir",
            str(tmp_path / "out"),
            "--keep-worktrees",
        ],
    )
    assert result.exit_code == 0
    assert captured["max_tokens"] == 2048
    assert captured["stream"] is True
    assert captured["thinking"] == '{"budget_tokens":128}'
    assert captured["unsafe"] is True
    assert captured["verbose"] is True
    assert captured["extra_json"] == '{"x":1}'
    assert captured["redact"] is True
    assert captured["dangerously_skip_permissions"] is True
    assert captured["auto_accept_edits"] is True
    assert captured["auto_approve"] is False
    assert captured["plan_mode"] == "strict"
    assert captured["max_repair_attempts"] == 5
    assert captured["small_model"] is True
    assert captured["provider"] == "openai"
    assert captured["api_key"] == "k"
    assert captured["debug"] == "trace"
    assert captured["workers"] == 4
    assert captured["verify_command"] == "pytest -q"
    assert captured["keep_worktrees"] is True
