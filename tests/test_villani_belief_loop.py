from __future__ import annotations

import json
from pathlib import Path

from villani_code.villani_actions import ActionKind, choose_best_action, propose_actions
from villani_code.villani_loop import detect_loop_signals, run_villani_loop
from villani_code.villani_observe import observe_workspace
from villani_code.villani_state import ActionResultSummary, FailureObservation, WorkspaceBeliefState


class SeqRunner:
    def __init__(self, repo: Path, steps: list[dict]) -> None:
        self.repo = repo
        self.steps = steps
        self.idx = 0

    def run(self, _prompt: str, execution_budget=None):
        step = self.steps[min(self.idx, len(self.steps) - 1)] if self.steps else {}
        self.idx += 1
        for rel, content in step.get("writes", []):
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {
            "response": {"content": [{"type": "text", "text": step.get("text", "ok")}]} ,
            "transcript": {"tool_results": step.get("tool_results", [])},
            "execution": {
                "intentional_changes": step.get("intentional_changes", []),
                "files_changed": step.get("files_changed", step.get("intentional_changes", [])),
                "validation_artifacts": step.get("validation_artifacts", []),
                "runner_failures": step.get("runner_failures", []),
            },
        }

    def run_villani_action(
        self,
        *,
        objective: str,
        belief_state: dict,
        chosen_action: dict,
        expected_evidence: list[str],
        focus_files: list[str] | None = None,
        known_failures: list[str] | None = None,
        execution_budget=None,
    ):
        assert objective
        assert isinstance(belief_state, dict)
        assert isinstance(chosen_action, dict)
        assert isinstance(expected_evidence, list)
        return self.run("villani action", execution_budget=execution_budget)


def test_initial_observation_builds_sensible_beliefs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    beliefs = observe_workspace(tmp_path, "build app")

    assert "src/app.py" in beliefs.likely_deliverables
    assert "tests/test_app.py" in beliefs.test_inventory


def test_validate_preferred_when_artifact_exists_without_validation() -> None:
    beliefs = WorkspaceBeliefState(objective="x", likely_deliverables=["src/app.py"])
    best = choose_best_action(propose_actions(beliefs))
    assert best.kind == ActionKind.VALIDATE


def test_repair_preferred_when_validation_failure_exists() -> None:
    beliefs = WorkspaceBeliefState(
        objective="x",
        likely_deliverables=["src/app.py"],
        unresolved_critical_issues=["pytest failed"],
        known_failures=[FailureObservation("pytest failed", "boom", "tool")],
    )
    best = choose_best_action(propose_actions(beliefs))
    assert best.kind == ActionKind.REPAIR


def test_summarize_or_stop_when_validated_and_low_progress() -> None:
    beliefs = WorkspaceBeliefState(
        objective="x",
        likely_deliverables=["src/app.py"],
        materially_satisfied=True,
        completion_confidence=0.9,
    )
    beliefs.validation_observations = []
    # emulate already validated status retained in confidence+satisfied
    kinds = [a.kind for a in propose_actions(beliefs)]
    assert ActionKind.SUMMARIZE in kinds or ActionKind.STOP in kinds


def test_loop_detection_catches_repetitive_no_value_behavior() -> None:
    beliefs = WorkspaceBeliefState(objective="x")
    for _ in range(4):
        beliefs.add_action_result(ActionResultSummary(action_kind="validate", success=False, changed_files=[]))
    signals = detect_loop_signals(beliefs)
    assert "repeated_action:validate" in signals
    assert "no_meaningful_changes" in signals


def test_scratch_files_do_not_count_as_deliverables(tmp_path: Path) -> None:
    (tmp_path / "debug_probe.py").write_text("print(1)\n", encoding="utf-8")
    beliefs = observe_workspace(tmp_path, "x")
    assert "debug_probe.py" in beliefs.scratch_artifacts
    assert "debug_probe.py" not in beliefs.likely_deliverables


def test_narrative_claims_do_not_override_missing_command_evidence(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    runner = SeqRunner(tmp_path, [{"text": "all tests passed", "tool_results": [], "validation_artifacts": []}])
    summary = run_villani_loop(runner, tmp_path, "x")
    assert summary["beliefs"]["validation_observations"] == []


def test_run_villani_mode_routes_to_new_controller(tmp_path: Path, monkeypatch) -> None:
    from villani_code.state import Runner

    class DummyClient:
        def create_message(self, _payload, stream):
            return {"content": [{"type": "text", "text": "ok"}]}

    called = {"v": False}

    def fake_loop(*, runner, repo, objective, event_callback):
        called["v"] = True
        return {"done_reason": "ok", "iterations": 1, "beliefs": {"completion_confidence": 1.0, "likely_deliverables": [], "unresolved_critical_issues": []}}

    monkeypatch.setattr("villani_code.state.run_villani_loop", fake_loop)
    r = Runner(client=DummyClient(), repo=tmp_path, model="m", villani_mode=True, stream=False)
    out = r.run_villani_mode()
    assert called["v"] is True
    assert "summary" in out


def test_repair_flow_happens_naturally(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    runner = SeqRunner(
        tmp_path,
        [
            {
                "tool_results": [{"content": json.dumps({"command": "pytest -q", "exit_code": 1}), "is_error": False}],
                "validation_artifacts": ['{"command":"pytest -q","exit":1}'],
            },
            {
                "tool_results": [{"content": json.dumps({"command": "pytest -q", "exit_code": 0}), "is_error": False}],
                "validation_artifacts": ['{"command":"pytest -q","exit":0}'],
                "intentional_changes": ["src/app.py"],
            },
        ],
    )
    summary = run_villani_loop(runner, tmp_path, "x")
    actions = [a["action_kind"] for a in summary["working_memory"]["recent_actions"]]
    assert any(kind in {"repair", "validate"} for kind in actions)


def test_autonomous_path_bypasses_legacy_task_mode_and_uses_core(monkeypatch, tmp_path: Path) -> None:
    from villani_code.state import Runner

    class DummyClient:
        def create_message(self, _payload, stream):
            return {"content": [{"type": "text", "text": "done"}]}

    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=True)
    monkeypatch.setattr("villani_code.state.classify_task_mode", lambda _s: (_ for _ in ()).throw(AssertionError("legacy classify called")))
    monkeypatch.setattr(runner, "_ensure_project_memory_and_plan", lambda _s: (_ for _ in ()).throw(AssertionError("legacy project memory called")))
    monkeypatch.setattr(runner, "_run_post_execution_validation", lambda _c: "validated")

    out = runner.run_villani_action(
        objective="fix",
        belief_state={"x": 1},
        chosen_action={"kind": "repair"},
        expected_evidence=["pytest 0"],
    )
    assert "transcript" in out
    assert "tool_results" in out["transcript"]


def test_legacy_non_villani_controller_still_usable(tmp_path: Path) -> None:
    from villani_code.autonomous import VillaniModeController

    class MinimalRunner:
        def __init__(self, repo: Path):
            self.repo = repo

        def run(self, _prompt: str, execution_budget=None):
            return {"response": {"content": [{"type": "text", "text": "done"}]}, "transcript": {"tool_results": []}, "execution": {"turns_used": 1, "tool_calls_used": 0, "elapsed_seconds": 0.01, "terminated_reason": "completed", "completed": True, "intentional_changes": [], "validation_artifacts": [], "runner_failures": [], "inspection_summary": ""}}

    c = VillaniModeController(MinimalRunner(tmp_path), tmp_path)
    assert c is not None


def test_non_villani_run_keeps_legacy_path(monkeypatch, tmp_path: Path) -> None:
    from villani_code.state import Runner
    from villani_code.planning import TaskMode

    class DummyClient:
        def create_message(self, _payload, stream):
            return {"content": [{"type": "text", "text": "ok"}]}

    runner = Runner(client=DummyClient(), repo=tmp_path, model="m", stream=False, villani_mode=False)
    flags = {"ensure": 0}

    monkeypatch.setattr(runner, "_ensure_project_memory_and_plan", lambda _s: flags.__setitem__("ensure", flags["ensure"] + 1))
    monkeypatch.setattr("villani_code.state.classify_task_mode", lambda _s: TaskMode.GENERAL)
    runner.run("do work")
    assert flags["ensure"] == 1


def test_belief_persistence_retains_evidence_relevant_memory(tmp_path: Path) -> None:
    from villani_code.villani_state import load_beliefs

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    runner = SeqRunner(
        tmp_path,
        [
            {
                "tool_results": [{"content": '{"command":"pytest -q","exit":1}', "is_error": False}],
                "validation_artifacts": ['{"command":"pytest -q","exit":1}'],
            },
            {
                "tool_results": [{"content": '{"command":"pytest -q","exit":0}', "is_error": False}],
                "validation_artifacts": ['{"command":"pytest -q","exit":0}'],
                "intentional_changes": ["src/app.py"],
            },
        ],
    )
    run_villani_loop(runner, tmp_path, "x")
    loaded = load_beliefs(tmp_path, "x")
    assert loaded is not None
    assert any(v.command == "pytest -q" for v in loaded.validation_observations)
