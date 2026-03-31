from __future__ import annotations

import json
from pathlib import Path

from villani_code.debug_recorder import DebugRecorder
from villani_code.project_memory import ensure_project_memory, load_repo_map
from villani_code.runtime_paths import get_repo_state_dir
from villani_code.transcripts import save_transcript


def test_repo_state_dir_is_stable(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "state-root"
    monkeypatch.setenv("VILLANI_STATE_DIR", str(root))
    repo = tmp_path / "repo"
    repo.mkdir()
    first = get_repo_state_dir(repo)
    second = get_repo_state_dir(repo)
    assert first == second
    assert str(first).startswith(str(root))


def test_project_memory_writes_outside_workspace(monkeypatch, tmp_path: Path) -> None:
    state_root = tmp_path / "runtime-state"
    monkeypatch.setenv("VILLANI_STATE_DIR", str(state_root))
    repo = tmp_path / "repo"
    repo.mkdir()
    files = ensure_project_memory(repo)
    assert all(str(path).startswith(str(state_root)) for path in files.values())
    assert not (repo / ".villani").exists()


def test_legacy_repo_map_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VILLANI_STATE_DIR", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    (repo / ".villani").mkdir(parents=True)
    payload = {"languages": ["python"]}
    (repo / ".villani" / "repo_map.json").write_text(json.dumps(payload), encoding="utf-8")
    assert load_repo_map(repo) == payload


def test_debug_bundle_records_core_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VILLANI_STATE_DIR", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("print('x')\n", encoding="utf-8")

    recorder = DebugRecorder(repo, enabled=True)
    recorder.start_run("s1", "do work", {"model": "test"})
    recorder.record_beliefs({"completion_confidence": 0.1}, "initial")
    recorder.record_action({"step_index": 1, "chosen_action": {"kind": "inspect"}, "rationale": "x"})
    recorder.record_event({"type": "tool_use", "name": "Bash"})
    recorder.record_command("1", "pytest -q", 1, "failed", "")
    recorder.finalize(exit_status="failed", stop_reason="test")

    bundle = recorder.bundle_dir
    assert bundle is not None
    required = [
        "session_meta.json",
        "beliefs_initial.json",
        "actions.jsonl",
        "events.jsonl",
        "commands.jsonl",
        "workspace_tree_initial.txt",
        "workspace_tree_final.txt",
        "debug_summary.md",
    ]
    for name in required:
        assert (bundle / name).exists()


def test_transcript_uses_runtime_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VILLANI_STATE_DIR", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    out = save_transcript(repo, {"requests": [], "responses": []})
    assert str(out).startswith(str(tmp_path / "state"))
