from pathlib import Path

from villani_code.benchmark.task_loader import load_benchmark_task, load_benchmark_tasks, load_task_pack_metadata, resolve_tasks_dir


def test_load_benchmark_task_parses_schema() -> None:
    task = load_benchmark_task(Path("benchmark_tasks/internal_regressions/single_enter_approval.json"))
    assert task.id == "single_enter_approval"
    assert task.validation_checks
    assert task.validation_checks[0].type.value == "command"


def test_load_benchmark_tasks_can_filter_by_id() -> None:
    tasks = load_benchmark_tasks(Path("benchmark_tasks/internal_regressions"), task_id="ctrl_c_exit")
    assert len(tasks) == 1
    assert tasks[0].id == "ctrl_c_exit"


def test_load_pack_metadata() -> None:
    pack = load_task_pack_metadata(Path("benchmark_tasks/internal_regressions"))
    assert pack.classification == "internal_regression"


def test_legacy_path_alias_resolves() -> None:
    resolved = resolve_tasks_dir(Path("benchmark_tasks/villani_code"))
    assert resolved.name in {"villani_code", "internal_regressions"}


def test_general_pack_loads() -> None:
    tasks = load_benchmark_tasks(Path("benchmark_tasks/general_coding"))
    assert len(tasks) >= 12


def test_constrained_pack_loads() -> None:
    tasks = load_benchmark_tasks(Path("benchmark_tasks/constrained_model"))
    assert len(tasks) >= 6
