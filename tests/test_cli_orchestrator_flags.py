from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from villani_code import cli


class _FakeRunner:
    def __init__(self) -> None:
        self._mission_id = "child1"

    def run(self, instruction: str):
        return {"response": {"content": [{"type": "text", "text": '{"mode":"direct","subtasks":[]}'}, {"type": "text", "text": "ignored"}]}}


def test_run_writes_machine_result_artifact(monkeypatch, tmp_path: Path) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "_build_runner", lambda *args, **kwargs: _FakeRunner())
    parent_calls: list[str] = []
    monkeypatch.setattr(cli, "set_current_mission_id", lambda repo, mission_id: parent_calls.append(mission_id))

    out = tmp_path / "result.json"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "hello",
            "--base-url",
            "http://example.com",
            "--model",
            "x",
            "--role",
            "supervisor",
            "--result-json-path",
            str(out),
            "--parent-mission-id",
            "parent1",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["role"] == "supervisor"
    assert payload["parent_mission_id"] == "parent1"
    assert payload["response_json"]["mode"] == "direct"
    assert parent_calls == ["parent1"]


def test_run_extracts_json_when_response_is_plain_string(monkeypatch, tmp_path: Path) -> None:
    class _StringRunner:
        _mission_id = "child2"

        def run(self, instruction: str):
            return {"response": "```json\n{\"mode\":\"direct\",\"subtasks\":[]}\n```"}

    runner = CliRunner()
    monkeypatch.setattr(cli, "_build_runner", lambda *args, **kwargs: _StringRunner())
    out = tmp_path / "result_string.json"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "hello",
            "--base-url",
            "http://example.com",
            "--model",
            "x",
            "--result-json-path",
            str(out),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["response_json"]["mode"] == "direct"
