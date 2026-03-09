from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.state import Runner


class _Client:
    def create_message(self, payload, stream):
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


def _runner(tmp_path: Path, cfg: BenchmarkRuntimeConfig | None = None) -> Runner:
    return Runner(client=_Client(), repo=tmp_path, model="m", stream=False, benchmark_config=cfg)


def test_benchmark_verification_uses_visible_command_and_not_defaults(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    cfg = BenchmarkRuntimeConfig(
        enabled=True,
        task_id="t",
        visible_verification=["python -c 'print(1)'"]
    )
    runner = _runner(tmp_path, cfg)
    runner._verification_baseline_changed = set()
    runner._git_changed_files = lambda: ["src/app.py"]  # type: ignore[assignment]

    out = runner._run_verification("edit")

    assert "python -c 'print(1)'" in out
    assert "tests/test_runner_defaults.py" not in out


def test_benchmark_verification_skips_when_no_code_change(tmp_path: Path) -> None:
    cfg = BenchmarkRuntimeConfig(enabled=True, task_id="t", visible_verification=["python -c 'print(1)'"])
    events: list[dict] = []
    runner = _runner(tmp_path, cfg)
    runner.event_callback = events.append
    runner._verification_baseline_changed = set()
    runner._git_changed_files = lambda: []  # type: ignore[assignment]

    out = runner._run_verification("edit")

    assert out == ""
    assert any(e.get("type") == "benchmark_verification_skipped_repeated" for e in events)
