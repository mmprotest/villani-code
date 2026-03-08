from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.policy import enforce_path_policy
from villani_code.benchmark.task_loader import load_task


TASK_CASES = {
    "bugfix_003_csv_quotes": ["src/app/csv_parser.py", "tests/test_csv_quotes.py", "src/app/cli.py"],
    "bugfix_006_markdown_escape": ["src/app/core.py", "src/app/__init__.py", "tests/test_core.py"],
    "localize_003_serializer_mismatch": ["src/app/a.py", "src/app/b.py", "src/app/serializer.py"],
    "localize_004_command_alias_registration": ["src/app/a.py", "src/app/aliases.py"],
    "localize_005_cache_invalidation": ["src/app/a.py", "src/app/cache.py"],
    "terminal_001_python_module_entry": ["app/main.py", "app/__main__.py", "pyproject.toml"],
    "terminal_004_artifact_generation_pipeline": ["Makefile", "app/__main__.py", "tests/test_basic.py"],
    "terminal_005_lint_invocation": ["Makefile", "pyproject.toml", "app/__main__.py"],
}


def test_known_false_negative_tasks_no_longer_flag_task_related_edits() -> None:
    suite = Path("benchmark_tasks/villani_bench_v1")
    for task_id, touched in TASK_CASES.items():
        task = load_task(suite / task_id)
        policy = enforce_path_policy(
            touched=touched,
            allowlist=task.allowlist_paths,
            forbidden=task.forbidden_paths,
            expected_paths=task.metadata.expected_files,
            family=task.family.value,
            task_type=task.metadata.task_type,
            allowed_support_files=task.metadata.allowed_support_files,
            allowed_support_globs=task.metadata.allowed_support_globs,
        )
        assert policy.violating_paths == [], task_id
