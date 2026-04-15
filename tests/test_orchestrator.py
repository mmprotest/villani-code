from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from villani_code.cli import app
from villani_code.orchestrator import OrchestratorConfig, run_orchestrator


def test_orchestrate_accepts_run_flags(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_orchestrator(config):
        captured["args"] = config.inherited_run_args
        return {"status": "success"}

    monkeypatch.setattr("villani_code.cli.run_orchestrator", fake_run_orchestrator)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "orchestrate",
            "do thing",
            "--base-url",
            "http://localhost:8000",
            "--model",
            "demo-model",
            "--repo",
            str(tmp_path),
            "--provider",
            "openai",
            "--api-key",
            "secret",
            "--max-tokens",
            "2048",
            "--debug",
            "trace",
            "--auto-approve",
            "--small-model",
        ],
    )
    assert result.exit_code == 0
    args = captured["args"]
    assert isinstance(args, list)
    assert "--provider" in args
    assert "openai" in args
    assert "--debug" in args


def test_orchestrator_forwards_flags_to_supervisor_workers_and_retries(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "a.py").write_text("print('x')\n", encoding="utf-8")
    calls: list[list[str]] = []
    worker_attempts = {"count": 0}

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        assert cwd == repo
        calls.append(cmd)
        role = cmd[cmd.index("--role") + 1]
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        if role == "supervisor":
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps({"subtasks": [{"id": "task_1", "goal": "edit", "success_criteria": [], "target_files": ["a.py"], "scope_hint": "small"}]}),
                encoding="utf-8",
            )
        else:
            worker_attempts["count"] += 1
            payload = {"status": "failed", "summary": "retry", "files_touched": ["a.py"], "recommended_verification": []}
            if worker_attempts["count"] > 1:
                payload = {"status": "success", "summary": "ok", "files_touched": ["a.py"], "recommended_verification": []}
                (repo / "a.py").write_text("print('y')\n", encoding="utf-8")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "parent-1")
    monkeypatch.setattr("villani_code.orchestrator.set_current_mission_id", lambda _repo, _id: None)

    summary = run_orchestrator(
        OrchestratorConfig(
            instruction="fix stuff",
            repo=repo,
            inherited_run_args=["--base-url", "u", "--model", "m", "--provider", "openai", "--debug", "trace"],
            max_worker_retries=1,
        )
    )
    assert summary["status"] == "success"
    assert len(calls) >= 3
    for cmd in calls:
        assert "--base-url" in cmd
        assert "u" in cmd
        assert "--provider" in cmd
        assert "openai" in cmd
        assert "--debug" in cmd
        assert "trace" in cmd


def test_supervisor_retry_on_invalid_result(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    attempts = {"count": 0}

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        attempts["count"] += 1
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if attempts["count"] == 1:
            out_path.write_text("{}", encoding="utf-8")
        else:
            out_path.write_text(json.dumps({"subtasks": [{"id": "task_1", "goal": "x"}]}), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "")
    summary = run_orchestrator(OrchestratorConfig(instruction="x", repo=repo, inherited_run_args=["--base-url", "u", "--model", "m"]))
    assert summary["total_subtasks"] == 1
    assert attempts["count"] >= 2


def test_snapshot_restore_after_failed_verification(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    target = repo / "a.py"
    target.write_text("before\n", encoding="utf-8")
    worker_count = {"count": 0}

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        if isinstance(cmd, str):
            class Proc:
                returncode = 1
                stdout = ""
                stderr = ""
            return Proc()
        role = cmd[cmd.index("--role") + 1]
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if role == "supervisor":
            out_path.write_text(json.dumps({"subtasks": [{"id": "task_1", "goal": "edit", "target_files": ["a.py"], "success_criteria": []}]}), encoding="utf-8")
        else:
            worker_count["count"] += 1
            target.write_text(f"bad-{worker_count['count']}\n", encoding="utf-8")
            payload = {"status": "success", "summary": "ok", "files_touched": ["a.py"], "recommended_verification": ["python -c 'import sys;sys.exit(1)'"]}
            out_path.write_text(json.dumps(payload), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "")
    run_orchestrator(OrchestratorConfig(instruction="x", repo=repo, inherited_run_args=["--base-url", "u", "--model", "m"], max_worker_retries=0))
    assert target.read_text(encoding="utf-8") == "before\n"


def test_success_criteria_not_executed(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "a.py").write_text("x\n", encoding="utf-8")
    seen_shell_commands: list[str] = []

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        if isinstance(cmd, str):
            seen_shell_commands.append(cmd)
            class Proc:
                returncode = 0
                stdout = ""
                stderr = ""
            return Proc()
        role = cmd[cmd.index("--role") + 1]
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if role == "supervisor":
            out_path.write_text(json.dumps({"subtasks": [{"id": "task_1", "goal": "edit", "success_criteria": ["echo SHOULD_NOT_RUN"], "target_files": ["a.py"]}]}), encoding="utf-8")
        else:
            out_path.write_text(json.dumps({"status": "success", "summary": "ok", "files_touched": ["a.py"], "recommended_verification": []}), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "")
    run_orchestrator(OrchestratorConfig(instruction="x", repo=repo, inherited_run_args=["--base-url", "u", "--model", "m"]))
    assert "echo SHOULD_NOT_RUN" not in seen_shell_commands


def test_no_final_verification_when_no_worker_success(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        role = cmd[cmd.index("--role") + 1]
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if role == "supervisor":
            out_path.write_text(json.dumps({"subtasks": [{"id": "task_1", "goal": "x"}]}), encoding="utf-8")
        else:
            out_path.write_text(json.dumps({"status": "failed", "summary": "nope", "files_touched": [], "recommended_verification": []}), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "")
    summary = run_orchestrator(OrchestratorConfig(instruction="x", repo=repo, inherited_run_args=["--base-url", "u", "--model", "m"], max_worker_retries=0))
    mission_id = summary["mission_id"]
    final_verification = json.loads((repo / ".villani_code" / "missions" / mission_id / "orchestrator" / "final_verification.json").read_text(encoding="utf-8"))
    assert final_verification["ran"] is False


def test_parent_mission_remains_authoritative(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    set_calls: list[str] = []

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        role = cmd[cmd.index("--role") + 1]
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if role == "supervisor":
            out_path.write_text(json.dumps({"subtasks": [{"id": "task_1", "goal": "x"}]}), encoding="utf-8")
        else:
            out_path.write_text(json.dumps({"status": "failed", "summary": "nope", "files_touched": [], "recommended_verification": []}), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "parent-xyz")
    monkeypatch.setattr("villani_code.orchestrator.set_current_mission_id", lambda _repo, mid: set_calls.append(mid))
    run_orchestrator(OrchestratorConfig(instruction="x", repo=repo, inherited_run_args=["--base-url", "u", "--model", "m"]))
    assert set_calls[-1] == "parent-xyz"


def test_supervisor_can_return_multiple_subtasks(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path

    def fake_subprocess_run(cmd, cwd=None, capture_output=True, text=True, timeout=None, **kwargs):
        role = cmd[cmd.index("--role") + 1]
        out_path = Path(cmd[cmd.index("--result-json-path") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if role == "supervisor":
            out_path.write_text(json.dumps({"subtasks": [{"id": "task_1", "goal": "x"}, {"id": "task_2", "goal": "y"}]}), encoding="utf-8")
        else:
            out_path.write_text(json.dumps({"status": "failed", "summary": "nope", "files_touched": [], "recommended_verification": []}), encoding="utf-8")

        class Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return Proc()

    monkeypatch.setattr("villani_code.orchestrator.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr("villani_code.orchestrator.get_current_mission_id", lambda _repo: "")
    summary = run_orchestrator(OrchestratorConfig(instruction="x", repo=repo, inherited_run_args=["--base-url", "u", "--model", "m"], max_workers=2))
    assert summary["total_subtasks"] == 2
