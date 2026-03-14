from __future__ import annotations

from pathlib import Path


from villani_code.benchmark.models import (
    BenchmarkTask,
    BenchmarkTrack,
    SuccessPolicy,
    TaskDifficulty,
    TaskFamily,
)
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.benchmark.runner import BenchmarkRunner
from villani_code.benchmark.reporting import aggregate_results
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


def test_benchmark_scope_expansion_allowlisted_second_target_permitted(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _benchmark_config())
    runner.small_model = True
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=0\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    runner._intended_targets = {"src/app.py"}

    err = runner._small_model_tool_guard("Patch", {"file_path": "tests/test_app.py", "patch": "x"})
    assert err is None


def test_aggregate_results_includes_new_constraint_metrics() -> None:
    from villani_code.benchmark.models import (
        BenchmarkRunResult,
        FairnessClassification,
        FailureReason,
        TaskDifficulty,
        TaskFamily,
        BenchmarkTrack,
        TelemetryQuality,
    )

    base = dict(
        task_id="t1",
        benchmark_track=BenchmarkTrack.CORE,
        task_family=TaskFamily.BUGFIX,
        task_difficulty=TaskDifficulty.EASY,
        task_language="python",
        task_checksum="abc",
        benchmark_bucket="b",
        task_type="bugfix",
        agent_name="a",
        adapter_name="x",
        adapter_version="1",
        adapter_capability="x",
        fairness_classification=FairnessClassification.EXACT_COMPARABLE,
        fairness_notes="",
        telemetry_capability="x",
        model_name="m",
        success=0,
        pass_rate=0.0,
        failed=1,
        timed_out=0,
        visible_pass=True,
        hidden_pass=False,
        runtime_seconds=1.0,
        wall_clock_seconds=1.0,
        timeout=False,
        failure_reason=FailureReason.FORBIDDEN_EDIT,
        touched_file_paths=["src/a.py"],
        files_touched=1,
        lines_added=1,
        lines_deleted=0,
        total_tokens=1,
        number_of_turns=1,
        tool_calls_total=1,
        file_reads=0,
        file_writes=1,
        patch_attempts=1,
        test_runs=0,
        retries_after_failure=0,
        first_pass_success=False,
        recovered_after_failed_attempt=False,
        expected_files_touched_count=1,
        actual_files_touched_count=1,
        touched_unexpected_files=False,
        verifications_run=[],
        runtime_stressors=[],
        telemetry_quality=TelemetryQuality.INFERRED,
        telemetry_field_quality_map={},
        self_corrected_after_failed_verify=None,
        recovery_success=False,
    )
    row1 = BenchmarkRunResult(**base)
    row2 = row1.model_copy(
        update={
            "task_id": "t2",
            "success": 1,
            "failed": 0,
            "visible_pass": True,
            "hidden_pass": True,
            "failure_reason": None,
            "recovery_success": True,
            "no_progress_termination": True,
            "self_corrected_after_failed_verify": True,
        }
    )
    row3 = row1.model_copy(update={"task_id": "t3", "recovery_success": None})
    import json

    agg = json.loads(aggregate_results([row1, row2, row3]))
    assert "forbidden_edit_rate" in agg["overall"]
    assert "hidden_pass_rate" in agg["overall"]
    assert "visible_only_rate" in agg["overall"]
    assert "unrelated_touch_rate" in agg["overall"]
    assert "verification_relevance_rate" in agg["overall"]
    assert "recovery_success_rate" in agg["overall"]
    assert "no_progress_collapse_rate" in agg["overall"]
    assert "self_corrected_after_failed_verify_rate" in agg["overall"]
    assert agg["overall"]["recovery_success_rate"] == 0.5
    assert agg["overall"]["no_progress_collapse_rate"] == 0.3333


def _minimal_task(tmp_path: Path, **overrides) -> BenchmarkTask:
    task_dir = tmp_path / "task"
    repo = task_dir / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.yaml").write_text("id: task_1\n", encoding="utf-8")
    (task_dir / "prompt.txt").write_text("fix\n", encoding="utf-8")
    (task_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app.py").write_text("x=0\n", encoding="utf-8")
    base = dict(
        id="task_1",
        benchmark_track=BenchmarkTrack.CORE,
        family=TaskFamily.BUGFIX,
        difficulty=TaskDifficulty.EASY,
        language="python",
        max_minutes=1,
        max_files_touched=3,
        visible_verification=["python -c 'print(1)'"],
        hidden_verification=["python -c 'print(1)'"],
        success_policy=SuccessPolicy(),
        allowlist_paths=["src/", "tests/"],
        task_dir=task_dir,
        prompt="fix",
    )
    base.update(overrides)
    return BenchmarkTask(**base)


def test_visible_only_and_hidden_verifier_scoring(tmp_path: Path, monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterRunResult
    from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality

    class FakeAgent:
        name = "villani"
        version = "1"
        capability = "x"
        telemetry_capability = "x"
        fairness_classification = FairnessClassification.EXACT_COMPARABLE
        fairness_notes = "x"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(stdout="", stderr="", exit_code=0, timeout=False, runtime_seconds=0.01, telemetry_quality=TelemetryQuality.INFERRED, telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED}, events=[])

    calls = {"n": 0}

    def fake_run_commands(_repo, _cmds, _timeout=None, timeout_seconds=None, **_kwargs):
        calls["n"] += 1
        return (True, [], 1.0, 2.0, False) if calls["n"] == 1 else (False, [], 2.0, 3.0, False)

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda _agent: FakeAgent())
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", fake_run_commands)
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda _repo: ["src/app.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda _repo: (1, 0))

    task = _minimal_task(tmp_path, hidden_verification=["python -c 'print(2)'"])
    task.metadata.expected_files = ["src/app.py"]
    row = BenchmarkRunner(output_dir=tmp_path / "out")._run_task(task, agent="villani", model="m", base_url=None, api_key=None, provider=None)
    assert row.visible_only_pass is True
    assert row.success == 0


def test_inspect_only_forbidden_unrelated_and_recovery_fields(tmp_path: Path, monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterRunResult
    from villani_code.benchmark.models import FairnessClassification, FieldQuality, TelemetryQuality

    class FakeAgent:
        name = "villani"
        version = "1"
        capability = "x"
        telemetry_capability = "x"
        fairness_classification = FairnessClassification.EXACT_COMPARABLE
        fairness_notes = "x"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(stdout="", stderr="", exit_code=0, timeout=False, runtime_seconds=0.01, telemetry_quality=TelemetryQuality.INFERRED, telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED}, events=[])

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda _agent: FakeAgent())
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda _repo, _cmds, _timeout, **_kwargs: (True, [], 1.0, 2.0, False))
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda _repo: ["docs/readme.md"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda _repo: (1, 0))

    task = _minimal_task(tmp_path, inspect_only=True, forbidden_paths=["docs/"], expected_touched_max=0)
    task.metadata.expected_files = ["src/app.py"]
    row = BenchmarkRunner(output_dir=tmp_path / "out")._run_task(task, agent="villani", model="m", base_url=None, api_key=None, provider=None)
    assert row.success == 0
    assert row.unrelated_file_touch is True
    assert row.verification_relevant is False
    assert row.recovery_attempted is False
    assert row.recovery_success is None


def test_inspect_only_uses_dedicated_failure_reason(tmp_path: Path, monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterRunResult
    from villani_code.benchmark.models import FailureReason, FairnessClassification, FieldQuality, TelemetryQuality

    class FakeAgent:
        name = "villani"
        version = "1"
        capability = "x"
        telemetry_capability = "x"
        fairness_classification = FairnessClassification.EXACT_COMPARABLE
        fairness_notes = "x"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(stdout="", stderr="", exit_code=0, timeout=False, runtime_seconds=0.01, telemetry_quality=TelemetryQuality.INFERRED, telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED}, events=[])

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda _agent: FakeAgent())
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", lambda _repo, _cmds, _timeout, **_kwargs: (True, [], 1.0, 2.0, False))
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda _repo: ["src/app.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda _repo: (1, 0))

    task = _minimal_task(tmp_path, inspect_only=True)
    row = BenchmarkRunner(output_dir=tmp_path / "out")._run_task(task, agent="villani", model="m", base_url=None, api_key=None, provider=None)
    assert row.failure_reason == FailureReason.INSPECT_ONLY_VIOLATION


def test_verification_relevant_unrelated_command_false() -> None:
    task = BenchmarkTask.model_validate({
        "id": "x",
        "benchmark_track": "core",
        "family": "bugfix",
        "difficulty": "easy",
        "language": "python",
        "max_minutes": 1,
        "max_files_touched": 2,
        "visible_verification": ["pytest -q tests/test_other.py"],
        "hidden_verification": ["python -m unrelated_mod"],
        "success_policy": {"require_visible_pass": True, "require_hidden_pass": True, "fail_on_timeout": True, "fail_on_repo_dirty_outside_allowlist": True},
        "allowlist_paths": ["src/"],
        "task_dir": str(Path("benchmark_tasks/villani_bench_v1/bugfix_001_datetime_cli")),
        "prompt": "fix",
    })
    task.metadata.expected_files = ["src/app.py"]
    assert BenchmarkRunner._verification_relevant(task, ["pytest -q tests/test_unrelated.py"], ["src/app.py"]) is False


def test_verification_relevant_touched_test_and_module_true(tmp_path: Path) -> None:
    task = _minimal_task(tmp_path)
    task.metadata.expected_files = ["src/app.py"]
    assert BenchmarkRunner._verification_relevant(task, ["pytest -q tests/test_app.py"], ["tests/test_app.py"]) is True
    assert BenchmarkRunner._verification_relevant(task, ["python -m src.app"], ["src/app.py"]) is True


def test_recovery_attempt_semantics() -> None:
    from villani_code.benchmark.models import FailureReason

    assert BenchmarkRunner._recovery_attempted(1, 1, False, FailureReason.HIDDEN_VERIFICATION_FAILED) is True
    assert BenchmarkRunner._recovery_attempted(0, 1, False, None) is False
    assert BenchmarkRunner._recovery_attempted(0, 2, True, FailureReason.VISIBLE_VERIFICATION_FAILED) is True


def test_benchmark_runtime_uses_diagnosis_step_without_new_flags(tmp_path: Path) -> None:
    cfg = _benchmark_config()

    class Client:
        def __init__(self):
            self.calls = 0

        def create_message(self, _payload, stream):
            assert stream is False
            self.calls += 1
            if self.calls == 1:
                return {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"target_file":"src/app.py","bug_class":"unknown","fix_intent":"Inspect failing path and patch minimal implementation."}',
                        }
                    ],
                }
            return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}

    client = Client()
    runner = Runner(client=client, repo=tmp_path, model="m", stream=False, benchmark_config=cfg)
    out = runner.run("fix benchmark bug")
    assert out["execution"]["terminated_reason"] in {"benchmark_incomplete_no_patch", "completed"}
    assert client.calls >= 2


def test_launch_failure_maps_to_verification_command_failed_to_launch(tmp_path: Path, monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterRunResult
    from villani_code.benchmark.models import FailureReason, FairnessClassification, FieldQuality, TelemetryQuality, VerificationOutcome

    class FakeAgent:
        name = "villani"
        version = "1"
        capability = "x"
        telemetry_capability = "x"
        fairness_classification = FairnessClassification.EXACT_COMPARABLE
        fairness_notes = "x"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(stdout="", stderr="", exit_code=0, timeout=False, runtime_seconds=0.01, telemetry_quality=TelemetryQuality.INFERRED, telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED}, events=[])

    def _launch_fail(_repo, _cmds, _timeout=None, timeout_seconds=None, **_kwargs):
        return False, [VerificationOutcome(command="pytest -q", passed=False, exit_code=None, stdout="", stderr="[launch-error]", started_at=1.0, finished_at=2.0)], 1.0, 2.0, True

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda _agent: FakeAgent())
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", _launch_fail)
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda _repo: ["src/app.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda _repo: (1, 0))

    task = _minimal_task(tmp_path)
    row = BenchmarkRunner(output_dir=tmp_path / "out")._run_task(task, agent="villani", model="m", base_url=None, api_key=None, provider=None)
    assert row.failure_reason == FailureReason.VERIFICATION_COMMAND_FAILED_TO_LAUNCH


def test_test_failure_stays_visible_verification_failed(tmp_path: Path, monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterRunResult
    from villani_code.benchmark.models import FailureReason, FairnessClassification, FieldQuality, TelemetryQuality, VerificationOutcome

    class FakeAgent:
        name = "villani"
        version = "1"
        capability = "x"
        telemetry_capability = "x"
        fairness_classification = FairnessClassification.EXACT_COMPARABLE
        fairness_notes = "x"
        supports_model_override = True

        def run_agent(self, **kwargs):
            return AdapterRunResult(stdout="", stderr="", exit_code=0, timeout=False, runtime_seconds=0.01, telemetry_quality=TelemetryQuality.INFERRED, telemetry_field_quality_map={"num_shell_commands": FieldQuality.INFERRED}, events=[])

    def _verify_fail(_repo, _cmds, _timeout=None, timeout_seconds=None, **_kwargs):
        return False, [VerificationOutcome(command="pytest -q", passed=False, exit_code=1, stdout="", stderr="assert failed", started_at=1.0, finished_at=2.0)], 1.0, 2.0, False

    monkeypatch.setattr("villani_code.benchmark.runner.build_agent_runner", lambda _agent: FakeAgent())
    monkeypatch.setattr("villani_code.benchmark.runner.run_commands", _verify_fail)
    monkeypatch.setattr("villani_code.benchmark.runner.list_touched_files", lambda _repo: ["src/app.py"])
    monkeypatch.setattr("villani_code.benchmark.runner.line_stats", lambda _repo: (1, 0))

    task = _minimal_task(tmp_path)
    row = BenchmarkRunner(output_dir=tmp_path / "out")._run_task(task, agent="villani", model="m", base_url=None, api_key=None, provider=None)
    assert row.failure_reason == FailureReason.VISIBLE_VERIFICATION_FAILED



def test_event_metrics_recognize_villani_native_runtime_events(tmp_path: Path) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent

    runner = BenchmarkRunner(output_dir=tmp_path / 'out')
    started = 100.0
    events = [
        AdapterEvent(type='tool_started', timestamp=101.0, payload={'type': 'tool_started', 'name': 'Read'}),
        AdapterEvent(type='tool_finished', timestamp=101.2, payload={'type': 'tool_finished', 'name': 'Read', 'is_error': False}),
        AdapterEvent(type='tool_result', timestamp=101.3, payload={'type': 'tool_result', 'name': 'Read', 'path': 'src/app.py', 'is_error': False}),
        AdapterEvent(type='tool_started', timestamp=102.0, payload={'type': 'tool_started', 'name': 'Write', 'path': 'src/app.py'}),
        AdapterEvent(type='benchmark_patch_blocked', timestamp=102.5, payload={'type': 'benchmark_patch_blocked', 'reason': 'benchmark_policy_denied: reason=outside_allowlist path=docs/x.md', 'paths': ['docs/x.md']}),
        AdapterEvent(type='model_request_started', timestamp=103.0, payload={'type': 'model_request_started'}),
    ]
    metrics = runner._event_metrics(
        events,
        started,
        expected_files=['src/app.py'],
        visible_commands=['pytest -q'],
        hidden_commands=[],
    )

    assert metrics['tool_calls_total']
    assert metrics['file_reads']
    assert metrics['file_writes']
    assert metrics['number_of_turns']
    assert metrics['benchmark_mutation_denials'] == 1
    assert metrics['first_denied_path'] == 'docs/x.md'


def test_extract_termination_reason_reads_villani_native_signals(tmp_path: Path) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent

    events = [
        AdapterEvent(type='tool_result', timestamp=1.0, payload={'type': 'tool_result'}),
        AdapterEvent(type='villani_stop_decision', timestamp=2.0, payload={'type': 'villani_stop_decision', 'done_reason': 'benchmark_incomplete_no_patch'}),
    ]
    assert BenchmarkRunner._extract_termination_reason(events) == 'benchmark_incomplete_no_patch'


def test_event_metrics_extract_denial_summary_fields(tmp_path: Path) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent

    runner = BenchmarkRunner(output_dir=tmp_path / 'out')
    metrics = runner._event_metrics(
        [
            AdapterEvent(
                type='benchmark_write_blocked',
                timestamp=1.0,
                payload={
                    'type': 'benchmark_write_blocked',
                    'reason': 'benchmark_policy_denied: task_id=t1 reason=not_expected_or_support path=tests/tmp.py',
                    'paths': ['tests/tmp.py'],
                },
            )
        ],
        started=0.0,
        expected_files=[],
        visible_commands=[],
        hidden_commands=[],
    )

    assert metrics['benchmark_mutation_denials'] == 1
    assert metrics['first_denied_path'] == 'tests/tmp.py'
    assert 'not_expected_or_support' in str(metrics['first_denied_reason'])



def test_benchmark_denial_feedback_is_actionable(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = _runner(tmp_path, _benchmark_config())
    runner.event_callback = events.append

    denied = execute_tool_with_policy(
        runner,
        'Write',
        {'file_path': 'docs/readme.md', 'content': 'x'},
        '1',
        0,
    )

    assert denied['is_error'] is True
    assert 'Denied path: docs/readme.md' in denied['content']
    assert 'Reason: outside_allowlist' in denied['content']
    assert 'Allowed expected/support targets:' in denied['content']
    assert any(e.get('type') == 'benchmark_write_blocked' and e.get('feedback') for e in events)


def test_benchmark_system_prompt_repro_emphasizes_regression_artifact(tmp_path: Path) -> None:
    cfg = _benchmark_config()
    cfg.require_patch_artifact = False
    blocks = build_system_blocks(tmp_path, benchmark_config=cfg)
    prompt_text = "\n".join(block["text"] for block in blocks)
    assert 'Repro-task emphasis' in prompt_text
    assert 'required regression test file' in prompt_text


def test_benchmark_post_write_python_validation_rejects_malformed_python(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = _runner(tmp_path, _benchmark_config())
    runner.event_callback = events.append

    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": "def broken(:\n    pass\n"},
        "1",
        0,
    )

    assert result["is_error"] is True
    assert "Benchmark post-write validation failed" in str(result["content"])
    assert any(e.get("type") == "benchmark_post_write_validation_failed" for e in events)


def test_benchmark_post_write_python_validation_accepts_valid_python(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = _runner(tmp_path, _benchmark_config())
    runner.event_callback = events.append

    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": "def ok() -> int:\n    return 1\n"},
        "1",
        0,
    )

    assert result["is_error"] is False
    assert not any(e.get("type") == "benchmark_post_write_validation_failed" for e in events)


def test_benchmark_post_write_validation_failure_surfaces_actionable_repair_context(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = _runner(tmp_path, _benchmark_config())
    runner.event_callback = events.append

    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": "# prose\nthis is not python !!!\n"},
        "1",
        0,
    )

    assert result["is_error"] is True
    text = str(result["content"])
    assert "Repair only this file with a minimal follow-up patch" in text
    assert "error=" in text


def test_benchmark_post_write_validation_failure_event_has_required_fields(tmp_path: Path) -> None:
    events: list[dict] = []
    runner = _runner(tmp_path, _benchmark_config())
    runner.event_callback = events.append

    _ = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": "def nope(\n"},
        "1",
        0,
    )

    failure_events = [e for e in events if e.get("type") == "benchmark_post_write_validation_failed"]
    assert failure_events
    event = failure_events[0]
    assert event["file_path"] == "src/app.py"
    assert event["validator"] == "py_compile"
    assert event["exception_type"]
    assert event["message"]


def test_benchmark_write_strips_fenced_python_wrapper_for_code_files(tmp_path: Path) -> None:
    runner = _runner(tmp_path, _benchmark_config())
    payload = "Here is fix:\n```python\ndef ok():\n    return 7\n```\n"
    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": payload},
        "1",
        0,
    )

    assert result["is_error"] is False
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "def ok():\n    return 7\n"
