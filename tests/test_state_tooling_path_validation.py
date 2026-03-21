from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.state import Runner
from villani_code.state_tooling import execute_tool_with_policy


class _Client:
    def create_message(self, _payload, stream):
        assert stream is False
        return {"role": "assistant", "content": [{"type": "text", "text": "done"}]}


class _Hooks:
    def run_event(self, *_args, **_kwargs):
        return type("Hook", (), {"allow": True, "reason": ""})()


class _PermissivePermissions:
    def evaluate_with_reason(self, *_args, **_kwargs):
        from villani_code.permissions import Decision

        return type("P", (), {"decision": Decision.ALLOW, "reason": ""})()


def _benchmark_config() -> BenchmarkRuntimeConfig:
    return BenchmarkRuntimeConfig(
        enabled=True,
        task_id="task_1",
        allowlist_paths=["src/"],
        forbidden_paths=[".git/"],
        expected_files=["src/app.py"],
        allowed_support_files=[],
        allowed_support_globs=[],
        max_files_touched=1,
        require_patch_artifact=True,
        visible_verification=["pytest -q"],
        hidden_verification=[],
    )


def _runner(tmp_path: Path) -> Runner:
    runner = Runner(client=_Client(), repo=tmp_path, model="m", stream=False, benchmark_config=_benchmark_config(), plan_mode="off")
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()
    return runner


def test_write_with_relative_path_still_creates_checkpoint_and_rewinds(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")

    result = execute_tool_with_policy(runner, "Write", {"file_path": "src/app.py", "content": "after\n"}, "1", 2)

    assert result["is_error"] is False
    assert runner._intended_targets == {"src/app.py"}
    checkpoints = runner.checkpoints.list()
    assert len(checkpoints) == 1
    assert checkpoints[0].files == ["src/app.py"]
    runner.checkpoints.rewind(checkpoints[0].id)
    assert target.read_text(encoding="utf-8") == "before\n"


def test_write_accepts_absolute_path_under_repo_and_normalizes_tracking(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("x=0\n", encoding="utf-8")

    result = execute_tool_with_policy(runner, "Write", {"file_path": str(target.resolve()), "content": "x=1\n"}, "1", 0)

    assert result["is_error"] is False
    assert runner._intended_targets == {"src/app.py"}
    assert runner._current_verification_targets == {"src/app.py"}
    checkpoints = runner.checkpoints.list()
    assert len(checkpoints) == 1
    assert checkpoints[0].files == ["src/app.py"]
    assert target.read_text(encoding="utf-8") == "x=1\n"


def test_write_rejects_absolute_path_outside_repo_without_crashing(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    outside = tmp_path.parent / "outside.py"

    result = execute_tool_with_policy(runner, "Write", {"file_path": str(outside.resolve()), "content": "x=1\n"}, "1", 0)

    assert result == {
        "content": f"Mutation target must stay under the active repo root: {outside.resolve()}",
        "is_error": True,
    }
    assert runner._intended_targets == set()
    assert runner._current_verification_targets == set()
    assert runner.checkpoints.list() == []
    assert not outside.exists()
