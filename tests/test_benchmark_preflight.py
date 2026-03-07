from pathlib import Path

from villani_code.benchmark.preflight import run_benchmark_preflight


def test_preflight_passes_for_known_pack_and_agent() -> None:
    result = run_benchmark_preflight(
        tasks_dir=Path("benchmark_tasks/general_coding"),
        repo_path=Path("."),
        agents=["villani"],
    )
    assert result.ok is True


def test_preflight_fails_for_unknown_agent() -> None:
    result = run_benchmark_preflight(
        tasks_dir=Path("benchmark_tasks/general_coding"),
        repo_path=Path("."),
        agents=["unknown-agent"],
    )
    assert result.ok is False
    assert any("Unknown agent names" in err for err in result.errors)


def test_preflight_fails_for_missing_repo() -> None:
    result = run_benchmark_preflight(
        tasks_dir=Path("benchmark_tasks/general_coding"),
        repo_path=Path("/definitely/missing/repo/path"),
        agents=["villani"],
    )
    assert result.ok is False
    assert any("Repository path does not exist" in err for err in result.errors)
