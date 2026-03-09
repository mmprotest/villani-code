from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.state import Runner


class SequenceClient:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    def create_message(self, payload, stream):
        out = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return out


def test_expected_file_read_emits_telemetry(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    events: list[dict] = []
    runner = Runner(
        client=SequenceClient([
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "src/app.py"}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]),
        repo=tmp_path,
        model="m",
        stream=False,
        benchmark_config=BenchmarkRuntimeConfig(enabled=True, task_id="t", task_type="single_file_bugfix", expected_files=["src/app.py"]),
        event_callback=events.append,
    )
    runner.run("fix")
    assert any(e.get("type") == "benchmark_expected_file_read" for e in events)


def test_expected_file_prompted_after_exploration(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = Runner(
        client=SequenceClient([
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Search", "input": {"query": "retry", "path": "."}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "2", "name": "Search", "input": {"query": "retry", "path": "."}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "3", "name": "Search", "input": {"query": "retry", "path": "."}}]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ]),
        repo=tmp_path,
        model="m",
        stream=False,
        benchmark_config=BenchmarkRuntimeConfig(enabled=True, task_id="t", task_type="single_file_bugfix", expected_files=["src/app.py"]),
        event_callback=events.append,
    )
    runner.run("fix")
    assert any(e.get("type") == "benchmark_expected_file_prompted" for e in events)
