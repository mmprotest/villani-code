from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.models import BenchmarkTask, BenchmarkTrack, SuccessPolicy, TaskDifficulty, TaskFamily
from villani_code.benchmark.prompt_contract import benchmark_contract_from_task, render_benchmark_prompt


def _task(tmp_path: Path) -> BenchmarkTask:
    repo = tmp_path / "task" / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("x=0\n", encoding="utf-8")
    task = BenchmarkTask(
        id="artifact_1",
        benchmark_track=BenchmarkTrack.CORE,
        family=TaskFamily.REPRO_TEST,
        difficulty=TaskDifficulty.EASY,
        language="python",
        max_minutes=2,
        max_files_touched=2,
        expected_artifacts=["patch", "tests/test_regression.py"],
        visible_verification=["pytest -q tests/test_regression.py"],
        hidden_verification=["python -m pytest tests/test_hidden.py -q"],
        success_policy=SuccessPolicy(),
        allowlist_paths=["src/", "tests/"],
        forbidden_paths=["docs/"],
        task_dir=tmp_path / "task",
        prompt="add a regression test",
    )
    task.metadata.expected_files = ["tests/test_regression.py"]
    task.metadata.allowed_support_files = ["src/app.py"]
    task.metadata.allowed_support_globs = ["tests/helpers/*.py"]
    return task


def test_contract_contains_agent_agnostic_fields(tmp_path: Path) -> None:
    task = _task(tmp_path)
    contract = benchmark_contract_from_task(task, tmp_path / "task" / "repo")

    assert contract.task_id == "artifact_1"
    assert contract.allowed_paths == ["src/", "tests/"]
    assert contract.forbidden_paths == ["docs/"]
    assert contract.expected_files == ["tests/test_regression.py"]
    assert contract.visible_verification_commands == ["pytest -q tests/test_regression.py"]
    assert contract.expected_artifacts == ["patch", "tests/test_regression.py"]


def test_render_prompt_includes_artifact_requirements(tmp_path: Path) -> None:
    task = _task(tmp_path)
    prompt = render_benchmark_prompt(task, tmp_path / "task" / "repo")

    assert "Benchmark task contract (shared across all agents):" in prompt
    assert "Objective: add a regression test" in prompt
    assert "Required final artifacts:" in prompt
    assert "- tests/test_regression.py" in prompt
    assert "Visible verification commands:" in prompt
    assert "- pytest -q tests/test_regression.py" in prompt
