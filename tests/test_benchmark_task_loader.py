from pathlib import Path

from villani_code.benchmark.task_loader import load_benchmark_task, load_benchmark_tasks


def test_load_benchmark_task_parses_schema() -> None:
    task = load_benchmark_task(Path("benchmark_tasks/villani_code/single_enter_approval.json"))
    assert task.id == "single_enter_approval"
    assert task.validation_checks
    assert task.validation_checks[0].type.value == "command"


def test_load_benchmark_tasks_can_filter_by_id() -> None:
    tasks = load_benchmark_tasks(Path("benchmark_tasks/villani_code"), task_id="ctrl_c_exit")
    assert len(tasks) == 1
    assert tasks[0].id == "ctrl_c_exit"
