from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.controller import WeakSearchController
from villani_code.runtime.policy import RuntimeStrategy, WeakSearchPolicyProfile


class DummyClient:
    def create_message(self, _payload, stream=False):
        return {"content": []}


class DummyRunner:
    def __init__(self, repo: Path):
        self.repo = repo
        self.client = DummyClient()
        self.model = "m"
        self.max_tokens = 64
        self.event_callback = lambda _e: None
        self.benchmark_config = BenchmarkRuntimeConfig(
            enabled=True,
            task_id="task-policy",
            allowlist_paths=["src/"],
            expected_files=["src/app.py"],
            max_files_touched=1,
            visible_verification=["pytest -q tests/test_app.py::test_fast"],
            task_family="bugfix",
            task_type="single_file_bugfix",
        )

    def _ensure_project_memory_and_plan(self, _instruction: str):
        return None


def test_controller_passes_real_task_family_and_type_to_policy(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    observed = {}

    def fake_policy(**kwargs):
        observed["task_family"] = kwargs.get("task_family")
        observed["task_type"] = kwargs.get("task_type")
        from villani_code.runtime.policy import PolicyDecision

        return PolicyDecision(profile=WeakSearchPolicyProfile.NORMAL_WEAK_SEARCH, strategy=RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE, reason="test")

    monkeypatch.setattr("villani_code.runtime.controller.decide_runtime_policy", fake_policy)

    WeakSearchController(DummyRunner(tmp_path), "fix bug").run()
    assert observed["task_family"] == "bugfix"
    assert observed["task_type"] == "single_file_bugfix"
