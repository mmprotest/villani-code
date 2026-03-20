from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.benchmark.tool_policy import (
    benchmark_denial_feedback,
    benchmark_mutation_targets,
    benchmark_post_write_python_validation,
    parse_benchmark_denial_message,
    validate_benchmark_mutation,
)


def _benchmark_config() -> BenchmarkRuntimeConfig:
    return BenchmarkRuntimeConfig(
        enabled=True,
        task_id="task_1",
        allowlist_paths=["src/", "tests/"],
        forbidden_paths=["src/private/", ".git/"],
        expected_files=["src/app.py"],
        allowed_support_files=["tests/test_app.py"],
        allowed_support_globs=["tests/helpers/*.py"],
        max_files_touched=2,
    )


def _runner(tmp_path: Path, cfg: BenchmarkRuntimeConfig | None = None) -> SimpleNamespace:
    events: list[dict] = []
    return SimpleNamespace(
        repo=tmp_path,
        benchmark_config=cfg or _benchmark_config(),
        event_callback=events.append,
        events=events,
    )


def test_benchmark_mutation_targets_for_write_and_patch() -> None:
    assert benchmark_mutation_targets("Write", {"file_path": "src/app.py"}) == ["src/app.py"]

    diff = (
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1 @@\n"
        "-x=0\n"
        "+x=1\n"
        "--- a/tests/test_app.py\n"
        "+++ b/tests/test_app.py\n"
        "@@ -1 +1 @@\n"
        "-assert False\n"
        "+assert True\n"
    )
    assert benchmark_mutation_targets("Patch", {"unified_diff": diff}) == ["src/app.py", "tests/test_app.py"]


def test_benchmark_mutation_targets_patch_falls_back_to_default_path_on_parse_error() -> None:
    assert benchmark_mutation_targets(
        "Patch",
        {"file_path": "src/app.py", "unified_diff": "not a diff"},
    ) == ["src/app.py"]


def test_validate_benchmark_mutation_denies_outside_allowlist_forbidden_and_max_files() -> None:
    runner = _runner(Path('.'))

    outside = validate_benchmark_mutation(runner, "Write", {"file_path": "docs/readme.md", "content": "x"})
    assert outside == "benchmark_policy_denied: task_id=task_1 reason=outside_allowlist path=docs/readme.md"

    forbidden = validate_benchmark_mutation(runner, "Write", {"file_path": "src/private/secret.py", "content": "x=1\n"})
    assert forbidden == "benchmark_policy_denied: task_id=task_1 reason=forbidden_path path=src/private/secret.py"

    multi = (
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1 +1 @@\n"
        "-x=0\n"
        "+x=1\n"
        "--- a/tests/test_app.py\n"
        "+++ b/tests/test_app.py\n"
        "@@ -1 +1 @@\n"
        "-assert False\n"
        "+assert True\n"
        "--- a/tests/helpers/helper.py\n"
        "+++ b/tests/helpers/helper.py\n"
        "@@ -1 +1 @@\n"
        "-pass\n"
        "+print('ok')\n"
    )
    too_many = validate_benchmark_mutation(runner, "Patch", {"unified_diff": multi})
    assert too_many == "benchmark_policy_denied: task_id=task_1 reason=max_files_touched_exceeded limit=2 touched=3"


def test_parse_benchmark_denial_message_and_feedback() -> None:
    runner = _runner(Path('.'))
    message = "benchmark_policy_denied: task_id=task_1 reason=outside_allowlist path=docs/readme.md"

    assert parse_benchmark_denial_message(message) == ("outside_allowlist", "docs/readme.md")

    feedback = benchmark_denial_feedback(runner, message, ["docs/readme.md"])
    assert "Denied path: docs/readme.md" in feedback
    assert "Reason: outside_allowlist" in feedback
    assert "src/app.py" in feedback
    assert "tests/test_app.py" in feedback


def test_benchmark_post_write_python_validation_returns_error_and_emits_expected_events(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    (tmp_path / "src").mkdir()
    broken = tmp_path / "src" / "app.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = benchmark_post_write_python_validation(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": "def broken(:\n    pass\n"},
        {"is_error": False, "content": "ok"},
    )

    assert result["is_error"] is True
    assert "Benchmark post-write validation failed" in str(result["content"])
    assert runner.events[0]["type"] == "benchmark_post_write_validation_failed"
    assert runner.events[0]["file_path"] == "src/app.py"
    assert runner.events[0]["validator"] == "py_compile"
    assert runner.events[1] == {
        "type": "failure_classified",
        "category": "benchmark_post_write_validation_failed",
        "summary": runner.events[0]["message"],
        "next_strategy": "Repair Python syntax in src/app.py and retry a minimal patch.",
        "occurrence": 1,
        "failed_files": ["src/app.py"],
    }
