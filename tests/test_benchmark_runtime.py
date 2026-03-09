from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from villani_code.benchmark.models import (
    BenchmarkTask,
    BenchmarkTrack,
    SuccessPolicy,
    TaskDifficulty,
    TaskFamily,
)
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.patch_apply import extract_unified_diff_targets
from villani_code.prompting import build_system_blocks
from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy


class _Client:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    def create_message(self, _payload, stream):
        assert stream is False
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class _Hooks:
    def run_event(self, *_args, **_kwargs):
        return type("Hook", (), {"allow": True, "reason": ""})()


class _PermissivePermissions:
    def evaluate_with_reason(self, *_args, **_kwargs):
        from villani_code.permissions import Decision

        return type("P", (), {"decision": Decision.ALLOW, "reason": ""})()


def _runner(tmp_path: Path, benchmark: BenchmarkRuntimeConfig | None = None) -> Runner:
    client = _Client([{"role": "assistant", "content": [{"type": "text", "text": "done"}]}])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, benchmark_config=benchmark, plan_mode="off")
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    return runner


def _benchmark_config() -> BenchmarkRuntimeConfig:
    return BenchmarkRuntimeConfig(
        enabled=True,
        task_id="task_1",
        allowlist_paths=["src/", "tests/"],
        forbidden_paths=[".git/"],
        expected_files=["src/app.py"],
        allowed_support_files=["tests/test_app.py"],
        allowed_support_globs=["tests/helpers/*.py"],
        max_files_touched=3,
        require_patch_artifact=True,
        visible_verification=["pytest -q"],
        hidden_verification=["python -m pytest tests/test_hidden.py -q"],
    )


def _init_repo_with_file(tmp_path: Path, path: str, content: str) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    file_path = tmp_path / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", path], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)


def test_benchmark_write_gating_expected_and_support_paths(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _benchmark_config())
    ok = execute_tool_with_policy(runner, "Write", {"file_path": "src/app.py", "content": "x=1\n"}, "1", 0)
    assert ok["is_error"] is False

    support = execute_tool_with_policy(runner, "Write", {"file_path": "tests/test_app.py", "content": "def test_x():\n    assert True\n"}, "2", 0)
    assert support["is_error"] is False


def test_benchmark_write_gating_blocks_out_of_scope_and_helper(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = _runner(tmp_path, _benchmark_config())
    runner.event_callback = events.append

    outside = execute_tool_with_policy(runner, "Write", {"file_path": "docs/readme.md", "content": "x"}, "1", 0)
    assert outside["is_error"] is True
    assert "outside_allowlist" in outside["content"]

    helper = execute_tool_with_policy(runner, "Write", {"file_path": "fix_bug.py", "content": "print(1)"}, "2", 0)
    assert helper["is_error"] is True
    assert "outside_allowlist" in helper["content"]
    assert any(e.get("type") == "benchmark_write_blocked" for e in events)


def test_benchmark_patch_gating_blocks_disallowed_and_multifile(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    runner = _runner(tmp_path, cfg)

    bad_patch = "--- a/fix_bug.py\n+++ b/fix_bug.py\n@@ -0,0 +1 @@\n+print(1)\n"
    blocked = execute_tool_with_policy(runner, "Patch", {"unified_diff": bad_patch}, "1", 0)
    assert blocked["is_error"] is True
    assert "outside_allowlist" in blocked["content"]

    multi = (
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -0,0 +1 @@\n"
        "+x=1\n"
        "--- a/fix_bug.py\n"
        "+++ b/fix_bug.py\n"
        "@@ -0,0 +1 @@\n"
        "+print(1)\n"
    )
    blocked_multi = execute_tool_with_policy(runner, "Patch", {"unified_diff": multi}, "2", 0)
    assert blocked_multi["is_error"] is True


def test_benchmark_patch_gating_uses_diff_targets_without_explicit_file_path(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    runner = _runner(tmp_path, cfg)
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("x=0\n", encoding="utf-8")
    diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=0\n+x=1\n"
    result = execute_tool_with_policy(runner, "Patch", {"unified_diff": diff}, "1", 0)
    assert result["is_error"] is False
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x=1\n"


def test_extract_unified_diff_targets_supports_new_delete_and_rename_hint() -> None:
    diff = (
        "--- a/src/a.py\n"
        "+++ b/src/b.py\n"
        "@@ -1 +1 @@\n"
        "-x=0\n"
        "+x=1\n"
        "--- a/src/old.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-pass\n"
        "--- /dev/null\n"
        "+++ b/src/new.py\n"
        "@@ -0,0 +1 @@\n"
        "+pass\n"
    )
    assert extract_unified_diff_targets(diff) == ["src/b.py", "src/old.py", "src/new.py"]


def test_benchmark_noop_completion_guard_blocks_then_terminates_incomplete(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    events: list[dict] = []
    client = _Client([
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "still done"}]},
    ])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, benchmark_config=cfg, event_callback=events.append)
    output = runner.run("fix bug")
    assert output["execution"]["completed"] is False
    assert output["execution"]["terminated_reason"] == "benchmark_incomplete_no_patch"
    assert any(e.get("type") == "benchmark_noop_completion_blocked" for e in events)


def test_benchmark_planning_text_before_first_tool_does_not_terminate(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    events: list[dict] = []
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "app.py").write_text("x=0\n", encoding="utf-8")
    diff = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-x=0\n+x=1\n"
    client = _Client([
        {"role": "assistant", "content": [{"type": "text", "text": "I will inspect the repository first."}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Patch", "input": {"unified_diff": diff}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, benchmark_config=cfg, event_callback=events.append)
    out = runner.run("fix bug")
    assert "execution" not in out or out["execution"]["terminated_reason"] != "benchmark_incomplete_no_patch"
    assert not any(e.get("type") == "benchmark_noop_completion_blocked" for e in events)


def test_benchmark_intermediate_text_after_reads_does_not_count_as_completion(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    _init_repo_with_file(tmp_path, "src/app.py", "x=0\n")
    events: list[dict] = []
    client = _Client([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "src/app.py"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "I found the likely issue and will patch next."}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "2", "name": "Write", "input": {"file_path": "src/app.py", "content": "x=1\n"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "task finished"}]},
    ])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, benchmark_config=cfg, event_callback=events.append)
    out = runner.run("fix bug")
    blocked_events = [e for e in events if e.get("type") == "benchmark_noop_completion_blocked"]
    assert blocked_events == []
    assert "execution" not in out or out["execution"]["terminated_reason"] != "benchmark_incomplete_no_patch"


def test_benchmark_post_mutation_completion_can_conclude(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    _init_repo_with_file(tmp_path, "src/app.py", "x=0\n")
    client = _Client([
        {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "src/app.py", "content": "x=1\n"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ])
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, benchmark_config=cfg)
    out = runner.run("fix bug")
    assert "execution" not in out or out["execution"]["terminated_reason"] != "benchmark_incomplete_no_patch"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x=1\n"


def test_non_benchmark_completion_unaffected(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    output = runner.run("say done")
    assert "execution" not in output or output["execution"]["completed"] is not False


def test_benchmark_system_prompt_includes_bounded_instructions(tmp_path: Path) -> None:
    blocks = build_system_blocks(tmp_path, benchmark_config=_benchmark_config())
    prompt_text = "\n".join(block["text"] for block in blocks)
    assert "bounded benchmark task" in prompt_text
    assert "Do not overfit visible checks" in prompt_text


def test_normal_system_prompt_has_no_benchmark_text(tmp_path: Path) -> None:
    blocks = build_system_blocks(tmp_path)
    prompt_text = "\n".join(block["text"] for block in blocks)
    assert "bounded benchmark task" not in prompt_text


def test_benchmark_runner_passes_runtime_config_to_native_agent(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "task" / "repo"
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x=0\n", encoding="utf-8")

    task = BenchmarkTask(
        id="task_1",
        benchmark_track=BenchmarkTrack.CORE,
        family=TaskFamily.BUGFIX,
        difficulty=TaskDifficulty.EASY,
        language="python",
        max_minutes=1,
        max_files_touched=2,
        visible_verification=["python -c 'print(1)'"],
        hidden_verification=["python -c 'print(1)'"],
        success_policy=SuccessPolicy(),
        allowlist_paths=["src/"],
        forbidden_paths=[".git/"],
        task_dir=tmp_path / "task",
        prompt="fix",
    )
    task.metadata.expected_files = ["src/app.py"]

    captured: dict[str, str | None] = {}

    class FakeAgent:
        name = "villani"
        version = "1"
        capability = "x"
        telemetry_capability = "x"
        fairness_classification = "exact_comparable"
        fairness_notes = "x"
        supports_model_override = True

        def run_agent(self, **kwargs):
            captured["payload"] = kwargs.get("benchmark_config_json")
            from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
            from villani_code.benchmark.models import FieldQuality, TelemetryQuality

            return AdapterRunResult(
                stdout="",
                stderr="",
                exit_code=0,
                timeout=False,
                runtime_seconds=0.01,
                telemetry_quality=TelemetryQuality.INFERRED,
                telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED},
                events=[AdapterEvent(type="command_started", timestamp=0.0, payload={})],
            )

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda _agent: FakeAgent())
    runner = BenchmarkRunner(output_dir=tmp_path / "out")
    result = runner._run_task(task, agent="villani", model="m", base_url="http://x", api_key="k", provider="openai")
    assert result.task_id == "task_1"
    assert captured["payload"] is not None
    assert '"task_id":"task_1"' in str(captured["payload"])
