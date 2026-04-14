from __future__ import annotations

import json
from pathlib import Path

from villani_code import orchestrator
from villani_code.orchestrator_models import VerificationResult


def _fake_mission(repo: Path):
    class _Mission:
        mission_id = "m1"

    return _Mission()


def _patch_common(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(orchestrator, "create_mission_state", lambda repo, objective, mode: _fake_mission(repo))
    monkeypatch.setattr(orchestrator, "get_mission_dir", lambda repo, mission_id: tmp_path / ".villani_code" / "missions" / mission_id)
    monkeypatch.setattr(orchestrator, "get_current_branch", lambda repo: "main")
    monkeypatch.setattr(orchestrator, "get_head_commit", lambda repo: "abc123")
    monkeypatch.setattr(orchestrator, "set_current_mission_id", lambda repo, mission_id: None)


def test_supervisor_direct_path(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt1", "b1"))
    monkeypatch.setattr(orchestrator, "commit_all", lambda *args, **kwargs: True)
    monkeypatch.setattr(orchestrator, "merge_branch", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(orchestrator, "run_final_verification", lambda repo: VerificationResult("accepted", "ok", [], []))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("accepted", "ok", [], ["a.py"]))

    def _run(**kwargs):
        result_path = Path(kwargs["result_json_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"response_json": {"mode": "direct", "subtasks": []}}
        if kwargs["role"] == "worker":
            payload = {"response_json": {"status": "success", "recommended_verification": []}}
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return {"run_dir": str(result_path.parent / "run"), "result_path": str(result_path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)

    summary = orchestrator.run_orchestrator(
        instruction="do it",
        repo=tmp_path,
        model="m",
        base_url="",
        provider="anthropic",
        api_key=None,
        max_tokens=100,
        small_model=False,
        debug_mode=False,
        debug_dir=None,
        max_subtasks=3,
        max_worker_retries=1,
        supervisor_timeout_seconds=60,
        worker_timeout_seconds=60,
    )
    assert summary["status"] == "completed"


def test_supervisor_split_path(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / f"wt-{args[3]}", f"b-{args[3]}"))
    monkeypatch.setattr(orchestrator, "commit_all", lambda *args, **kwargs: True)
    monkeypatch.setattr(orchestrator, "merge_branch", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(orchestrator, "run_final_verification", lambda repo: VerificationResult("accepted", "ok", [], []))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("accepted", "ok", [], ["x.py"]))

    def _run(**kwargs):
        result_path = Path(kwargs["result_json_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if kwargs["role"] == "supervisor":
            payload = {"response_json": {"mode": "split", "subtasks": [{"id": "task_1", "goal": "g1", "success_criteria": [], "target_files": []}]}}
        else:
            payload = {"response_json": {"status": "success", "recommended_verification": []}}
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return {"run_dir": str(result_path.parent / "run"), "result_path": str(result_path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="split",
        repo=tmp_path,
        model="m",
        base_url="",
        provider="anthropic",
        api_key=None,
        max_tokens=100,
        small_model=False,
        debug_mode=False,
        debug_dir=None,
        max_subtasks=3,
        max_worker_retries=1,
        supervisor_timeout_seconds=60,
        worker_timeout_seconds=60,
    )
    assert summary["status"] == "completed"


def test_invalid_supervisor_result_artifact(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    calls = {"count": 0}

    def _run(**kwargs):
        calls["count"] += 1
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if calls["count"] == 1:
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_text(json.dumps({"response_json": {"mode": "direct", "subtasks": []}}), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("hard_failure", "no diff", [], []))

    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=1, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert calls["count"] >= 2
    assert summary["status"] == "failed"


def test_supervisor_invalid_twice_then_fail(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"response_json": {"mode": "oops", "subtasks": []}}), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=1, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"
    assert "Supervisor failed" in summary["summary"]


def test_worker_retry_flow(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "commit_all", lambda *args, **kwargs: True)
    monkeypatch.setattr(orchestrator, "merge_branch", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(orchestrator, "run_final_verification", lambda repo: VerificationResult("accepted", "ok", [], []))

    attempts = {"n": 0}

    def _verify(*args, **kwargs):
        attempts["n"] += 1
        return VerificationResult("retryable_failure", "retry", [], []) if attempts["n"] == 1 else VerificationResult("accepted", "ok", [], ["f.py"])

    monkeypatch.setattr(orchestrator, "verify_worker_result", _verify)

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"response_json": {"mode": "direct", "subtasks": []}} if kwargs["role"] == "supervisor" else {"response_json": {"status": "failed"}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=1, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert attempts["n"] == 2
    assert summary["status"] == "completed"


def test_worker_blocked_environment_handling(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("retryable_failure", "env", [], []))

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"response_json": {"mode": "direct", "subtasks": []}} if kwargs["role"] == "supervisor" else {"response_json": {"status": "blocked_environment"}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=0, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"


def test_worker_blocked_scope_handling(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("hard_failure", "scope", [], []))

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"response_json": {"mode": "direct", "subtasks": []}} if kwargs["role"] == "supervisor" else {"response_json": {"status": "blocked_scope"}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=1, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"


def test_worker_timeout_handling(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"response_json": {"mode": "direct", "subtasks": []} if kwargs["role"] == "supervisor" else {"status": "failed"}}), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": kwargs["role"] == "worker"}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=0, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"


def test_merge_failure_handling(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("accepted", "ok", [], ["f.py"]))
    monkeypatch.setattr(orchestrator, "commit_all", lambda *args, **kwargs: True)
    monkeypatch.setattr(orchestrator, "merge_branch", lambda *args, **kwargs: (False, "conflict"))

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"response_json": {"mode": "direct", "subtasks": []} if kwargs["role"] == "supervisor" else {"status": "success"}}), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=0, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"


def test_final_verification_failure_handling(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("accepted", "ok", [], ["f.py"]))
    monkeypatch.setattr(orchestrator, "commit_all", lambda *args, **kwargs: True)
    monkeypatch.setattr(orchestrator, "merge_branch", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(orchestrator, "run_final_verification", lambda repo: VerificationResult("hard_failure", "bad", [], []))

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"response_json": {"mode": "direct", "subtasks": []} if kwargs["role"] == "supervisor" else {"status": "success"}}), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=0, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"


def test_no_final_verification_when_no_worker_accepted(monkeypatch, tmp_path: Path) -> None:
    _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "create_worktree", lambda *args, **kwargs: (tmp_path / "wt", "b"))
    monkeypatch.setattr(orchestrator, "verify_worker_result", lambda *args, **kwargs: VerificationResult("hard_failure", "bad", [], []))

    called = {"n": 0}

    def _final(repo):
        called["n"] += 1
        return VerificationResult("accepted", "ok", [], [])

    monkeypatch.setattr(orchestrator, "run_final_verification", _final)

    def _run(**kwargs):
        path = Path(kwargs["result_json_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"response_json": {"mode": "direct", "subtasks": []} if kwargs["role"] == "supervisor" else {"status": "failed"}}), encoding="utf-8")
        return {"run_dir": str(path.parent / "run"), "result_path": str(path), "timed_out": False}

    monkeypatch.setattr(orchestrator, "run_villani_subprocess", _run)
    summary = orchestrator.run_orchestrator(
        instruction="x", repo=tmp_path, model="m", base_url="", provider="anthropic", api_key=None, max_tokens=1, small_model=False,
        debug_mode=False, debug_dir=None, max_subtasks=3, max_worker_retries=0, supervisor_timeout_seconds=60, worker_timeout_seconds=60
    )
    assert summary["status"] == "failed"
    assert called["n"] == 0
