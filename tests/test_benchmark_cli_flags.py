from __future__ import annotations

import json

from typer.testing import CliRunner

from villani_code.cli import app


def _benchmark_callback():
    command = next(cmd for cmd in app.registered_commands if cmd.name == "benchmark")
    return command.callback


def test_benchmark_cli_quiet_disables_verbose_and_stream(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, output_dir, verbose, stream_agent_output):
            captured["verbose"] = verbose
            captured["stream_agent_output"] = stream_agent_output

        def run(self, **kwargs):
            return {"output_dir": str(tmp_path / "out"), "metadata": {}, "results": []}

    benchmark_callback = _benchmark_callback()
    monkeypatch.setitem(benchmark_callback.__globals__, "BenchmarkRunner", FakeRunner)

    runner = CliRunner()
    result = runner.invoke(app, ["benchmark", "--quiet", "--repo", str(tmp_path), "--tasks-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert captured["verbose"] is False
    assert captured["stream_agent_output"] is False
    payload = json.loads(result.stdout)
    assert payload["results"] == []


def test_benchmark_cli_defaults_to_verbose_and_stream(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeRunner:
        def __init__(self, output_dir, verbose, stream_agent_output):
            captured["verbose"] = verbose
            captured["stream_agent_output"] = stream_agent_output

        def run(self, **kwargs):
            return {"output_dir": str(tmp_path / "out"), "metadata": {}, "results": []}

    benchmark_callback = _benchmark_callback()
    monkeypatch.setitem(benchmark_callback.__globals__, "BenchmarkRunner", FakeRunner)

    runner = CliRunner()
    result = runner.invoke(app, ["benchmark", "--repo", str(tmp_path), "--tasks-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert captured["verbose"] is True
    assert captured["stream_agent_output"] is True
