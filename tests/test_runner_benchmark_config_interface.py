from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class _StructuralBenchmarkConfig:
    enabled: bool = True
    task_id: str = "task_structural"
    allowlist_paths: list[str] = field(default_factory=lambda: ["src/", "tests/"])
    forbidden_paths: list[str] = field(default_factory=lambda: [".git/"])
    expected_files: list[str] = field(default_factory=lambda: ["src/app.py"])
    allowed_support_files: list[str] = field(default_factory=lambda: ["tests/test_app.py"])
    allowed_support_globs: list[str] = field(default_factory=lambda: ["tests/helpers/*.py"])
    max_files_touched: int = 2
    require_patch_artifact: bool = True
    visible_verification: list[str] = field(default_factory=lambda: ["pytest -q"])
    hidden_verification: list[str] = field(default_factory=list)

    def normalized_path(self, raw_path: str) -> str:
        return str(raw_path or "").replace("\\", "/").lstrip("./")

    def in_allowlist(self, raw_path: str) -> bool:
        path = self.normalized_path(raw_path)
        return any(path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/") for scope in self.allowlist_paths)

    def in_forbidden(self, raw_path: str) -> bool:
        path = self.normalized_path(raw_path)
        return any(path == scope.rstrip("/") or path.startswith(scope.rstrip("/") + "/") for scope in self.forbidden_paths)

    def is_expected_or_support(self, raw_path: str) -> bool:
        path = self.normalized_path(raw_path)
        return path in {self.normalized_path(item) for item in [*self.expected_files, *self.allowed_support_files]}


def test_runner_accepts_structural_benchmark_config(tmp_path: Path) -> None:
    config = _StructuralBenchmarkConfig()
    runner = Runner(
        client=_Client(),
        repo=tmp_path,
        model="m",
        stream=False,
        benchmark_config=config,
        plan_mode="off",
    )
    runner.hooks = _Hooks()
    runner.permissions = _PermissivePermissions()

    result = execute_tool_with_policy(
        runner,
        "Write",
        {"file_path": "src/app.py", "content": "def ok() -> int:\n    return 1\n"},
        "1",
        0,
    )

    assert runner.benchmark_config is config
    assert result["is_error"] is False
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "def ok() -> int:\n    return 1\n"
