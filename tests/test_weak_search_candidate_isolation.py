from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.runtime.candidate_executor import CandidateExecutor
from villani_code.runtime.controller import WeakSearchController


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.payloads = []

    def create_message(self, payload, stream=False):
        self.payloads.append(payload)
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class DummyRunner:
    def __init__(self, repo: Path, client=None, bench: BenchmarkRuntimeConfig | None = None):
        self.repo = repo
        self.client = client or ScriptedClient([{"content": []}])
        self.benchmark_config = bench or BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], expected_files=["src/app.py"], visible_verification=["python -c \"print(1)\""])
        self.event_callback = lambda _e: None
        self.model = "m"
        self.max_tokens = 256

    def _ensure_project_memory_and_plan(self, _instruction: str) -> None:
        return None

    def _execute_tool_with_policy(self, name: str, inp: dict, _tool_id: str, _msg_count: int):
        if name == "Write":
            path = self.repo / inp["file_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(inp["content"], encoding="utf-8")
            return {"content": "ok", "is_error": False}
        return {"content": "noop", "is_error": False}


def _eval(ex: CandidateExecutor, repo: Path, attempt_id: str, verify_cmd: str) -> object:
    return ex.evaluate_candidate(
        repo_path=repo,
        objective="fix",
        suspect_region="src/app.py",
        hypothesis_id=f"h-{attempt_id}",
        hypothesis="edit",
        constraints={},
        runtime_profile="benchmark",
        benchmark_config=BenchmarkRuntimeConfig(enabled=True, allowlist_paths=["src/"], expected_files=["src/app.py"], visible_verification=[verify_cmd]),
        baseline_handle="clean",
        edit_budget=ex.edit_budget,
        branch_failure_history=[],
        timeout_budget_seconds=30.0,
        attempt_id=attempt_id,
    )


def test_failed_candidate_does_not_mutate_real_repo(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    client = ScriptedClient([
        {"content": [{"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "src/app.py", "content": "x=2\n"}}]},
        {"content": [{"type": "text", "text": "done"}]},
    ])
    ex = CandidateExecutor(DummyRunner(tmp_path, client=client), "fix", 20, 1)
    result = _eval(ex, tmp_path, "att-fail", "python -c \"import sys;sys.exit(1)\"")
    assert result.patch_artifact_path
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x=1\n"


def test_only_committed_winner_mutates_repo(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    client = ScriptedClient([
        {"content": [{"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "src/app.py", "content": "x=2\n"}}]},
        {"content": [{"type": "text", "text": "done"}]},
    ])
    ex = CandidateExecutor(DummyRunner(tmp_path, client=client), "fix", 20, 1)
    winner = _eval(ex, tmp_path, "att-win", "python -c \"print(1)\"")
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x=1\n"
    ex.commit_candidate(tmp_path, winner)
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "x=2\n"


def test_iterative_model_tool_loop_runs_multiple_turns(tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    client = ScriptedClient([
        {"content": [{"type": "tool_use", "id": "1", "name": "Write", "input": {"file_path": "src/app.py", "content": "x=2\n"}}]},
        {"content": [{"type": "text", "text": "final"}]},
    ])
    ex = CandidateExecutor(DummyRunner(tmp_path, client=client), "fix", 20, 1)
    _eval(ex, tmp_path, "att-loop", "python -c \"print(1)\"")
    assert client.calls >= 2
    assert any(m.get("role") == "user" and any(b.get("type") == "tool_result" for b in m.get("content", [])) for m in client.payloads[1]["messages"])


def test_candidate_isolation_event_contract(monkeypatch, tmp_path: Path):
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x=1\n", encoding="utf-8")
    events = []

    class EventRunner(DummyRunner):
        def __init__(self, repo: Path):
            super().__init__(repo)
            self.event_callback = events.append

    def reject_eval(self, **kwargs):
        from villani_code.runtime.candidate_executor import CandidateExecutionResult

        return CandidateExecutionResult(hard_fail=True, attempt_category="blocked_policy", blocked_reason="blocked_policy", failure_signature="sig")

    monkeypatch.setattr("villani_code.runtime.candidate_executor.CandidateExecutor.evaluate_candidate", reject_eval)
    WeakSearchController(EventRunner(tmp_path), "fix").run()
    types = {e.get("type") for e in events}
    assert "candidate_patch_rejected" in types
    assert "candidate_patch_discarded" in types
    assert "candidate_patch_committed" not in types
