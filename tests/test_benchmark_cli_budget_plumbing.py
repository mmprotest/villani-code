from __future__ import annotations

from villani_code import cli


class _Runner:
    def __init__(self):
        self.execution_budget = None

    def run(self, instruction: str, execution_budget=None):
        self.execution_budget = execution_budget
        return {"response": {"content": []}}


def test_cli_run_passes_budget_when_benchmark_runtime_json(monkeypatch) -> None:
    holder: dict[str, object] = {}

    def fake_build_runner(*args, **kwargs):
        runner = _Runner()
        holder["runner"] = runner
        return runner

    monkeypatch.setattr(cli, "_build_runner", fake_build_runner)

    cfg = '{"enabled":true,"task_id":"t","task_type":"single_file_bugfix"}'
    cli.run(
        instruction="fix",
        base_url="http://x",
        model="m",
        benchmark_runtime_json=cfg,
    )
    assert holder["runner"].execution_budget is not None


def test_cli_run_non_benchmark_does_not_pass_budget(monkeypatch) -> None:
    holder: dict[str, object] = {}

    def fake_build_runner(*args, **kwargs):
        runner = _Runner()
        holder["runner"] = runner
        return runner

    monkeypatch.setattr(cli, "_build_runner", fake_build_runner)

    cli.run(
        instruction="fix",
        base_url="http://x",
        model="m",
        benchmark_runtime_json=None,
    )
    assert holder["runner"].execution_budget is None


def test_cli_run_passes_explicit_execution_budget_json(monkeypatch) -> None:
    holder: dict[str, object] = {}

    def fake_build_runner(*args, **kwargs):
        runner = _Runner()
        holder["runner"] = runner
        return runner

    monkeypatch.setattr(cli, "_build_runner", fake_build_runner)

    cli.run(
        instruction="fix",
        base_url="http://x",
        model="m",
        benchmark_runtime_json=None,
        execution_budget_json='{"max_turns":11,"max_tool_calls":22,"max_seconds":123.0,"max_no_edit_turns":7,"max_reconsecutive_recon_turns":5}',
    )
    budget = holder["runner"].execution_budget
    assert budget is not None
    assert budget.max_turns == 11
