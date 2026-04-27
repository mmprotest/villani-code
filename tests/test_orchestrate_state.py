from pathlib import Path

from villani_code.orchestrate.state import OrchestrateState


def test_orchestrate_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "artifacts" / "state.json"
    state = OrchestrateState(
        original_task="fix bug",
        success_criteria=["tests pass"],
        constraints=["minimal"],
        files_in_scope=["src/app.py"],
        completed_rounds=1,
    )
    state.save(path)
    loaded = OrchestrateState.load(path)
    assert loaded.original_task == "fix bug"
    assert loaded.success_criteria == ["tests pass"]
    assert loaded.files_in_scope == ["src/app.py"]
