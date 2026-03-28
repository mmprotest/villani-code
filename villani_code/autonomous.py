from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from villani_code.autonomous_reporting import build_mission_summary
from villani_code.autonomous_stop import evaluate_mission_stop
from villani_code.change_containment import build_change_containment_context, create_regression_containment_nodes
from villani_code.localization import LocalizationEngine
from villani_code.mission import Mission, MissionExecutionState, MissionType, NodeStatus
from villani_code.mission_bridge import execute_mission_node_with_runner
from villani_code.mission_planner import MissionPlanner
from villani_code.mission_store import append_mission_event, save_final_mission_report, save_mission_snapshot
from villani_code.recovery import RecoveryPlanner
from villani_code.repo_signal_planner import collect_repo_signals
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.verification import (
    classify_node_outcome,
    evaluate_mission_status,
    run_static_verification,
    run_validation_commands,
)


@dataclass(slots=True)
class VillaniModeConfig:
    enabled: bool = False
    steering_objective: str | None = None


class VillaniModeController:
    """Mission-driven autonomous Villani mode that uses the existing runner as execution engine."""

    def __init__(
        self,
        runner: Any,
        repo: Path,
        steering_objective: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.runner = runner
        self.repo = repo.resolve()
        ensure_runtime_dependencies_not_shadowed(self.repo)
        self.steering_objective = steering_objective
        self.event_callback = event_callback or (lambda _event: None)
        self.planner = MissionPlanner()
        self.localization = LocalizationEngine(self.repo)
        self.recovery = RecoveryPlanner(self.planner)
        self._repo_signals: dict[str, Any] = {}

    def run(self) -> dict[str, Any]:
        mission_state = self._initialize_mission()
        save_mission_snapshot(str(self.repo), mission_state.mission, mission_state.to_dict())

        while True:
            done = self._mission_done(mission_state)
            if done is not None:
                break
            node = self._select_next_node(mission_state)
            if node is None:
                mission_state.consecutive_no_progress += 1
                continue
            result = self._execute_node(mission_state, node)
            outcome = self._evaluate_node(mission_state, node, result)
            if outcome.get("status") in {"failed", "stale", "partial"}:
                self._handle_recovery(mission_state, node, outcome)

            save_mission_snapshot(str(self.repo), mission_state.mission, mission_state.to_dict())

        return self._finalize_mission(mission_state)

    def _initialize_mission(self) -> MissionExecutionState:
        objective = (self.steering_objective or "").strip()
        self._repo_signals = self._collect_repo_signals()
        mission = self.planner.build_mission(objective, str(self.repo), repo_signals=self._repo_signals)

        if mission.mission_type == MissionType.REGRESSION_CONTAINMENT:
            context = build_change_containment_context(str(self.repo))
            containment_nodes = create_regression_containment_nodes(mission, context)
            mission.nodes = containment_nodes

        if not objective:
            mission.user_goal = "Perform maintenance patrol and stabilize highest-leverage issue."
            mission.mission_type = MissionType.MAINTENANCE

        baseline = self._git_changed_files()
        state = MissionExecutionState(mission=mission, changed_files_baseline=baseline)
        append_mission_event(str(self.repo), mission.mission_id, {"type": "mission_initialized", "goal": mission.user_goal, "mission_type": mission.mission_type.value})
        return state

    def _collect_repo_signals(self) -> dict[str, Any]:
        return collect_repo_signals(str(self.repo))

    def _select_next_node(self, execution_state: MissionExecutionState):
        mission = execution_state.mission
        for node in mission.nodes:
            if node.status == NodeStatus.PENDING and all(self._node_by_id(mission, dep).status == NodeStatus.SUCCEEDED for dep in node.depends_on if self._node_by_id(mission, dep)):
                node.status = NodeStatus.READY
        ready_nodes = [n for n in mission.nodes if n.status == NodeStatus.READY]
        if not ready_nodes:
            return None
        return sorted(ready_nodes, key=lambda n: (n.priority, n.confidence), reverse=True)[0]

    def _execute_node(self, execution_state: MissionExecutionState, node: Any) -> dict[str, Any]:
        node.status = NodeStatus.RUNNING
        node.attempts += 1
        execution_state.active_node_id = node.node_id
        mission_result = execute_mission_node_with_runner(self.runner, execution_state.mission, node, execution_state)
        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "node_executed", "node_id": node.node_id, "changed_files": mission_result.changed_files})
        return {
            "runner_result": mission_result,
            "changed_files": mission_result.changed_files,
            "commands": mission_result.commands,
            "failures": mission_result.failures,
            "prose_only": mission_result.prose_only,
        }

    def _evaluate_node(self, execution_state: MissionExecutionState, node: Any, node_result: dict[str, Any]) -> dict[str, Any]:
        changed_files = list(node_result.get("changed_files", []))
        static_result = run_static_verification(str(self.repo), changed_files)
        commands = node.validation_commands or self._repo_signals.get("likely_validation_commands", ["pytest -q"])
        command_results = run_validation_commands(str(self.repo), commands[:2]) if commands else []
        outcome = classify_node_outcome(node.contract_type, static_result, command_results, changed_files, prose_only=bool(node_result.get("prose_only")))

        if command_results:
            for cr in command_results:
                if cr.get("failure_fingerprint"):
                    node.failure_fingerprint = str(cr.get("failure_fingerprint"))
                    break

        node.evidence.extend(static_result.get("findings", []))
        node.evidence.extend([f"cmd:{r.get('command')} exit={r.get('exit')}" for r in command_results])
        execution_state.verification_history.append({"node_id": node.node_id, "static": static_result, "commands": command_results, "outcome": outcome})

        if outcome["status"] == "passed":
            node.status = NodeStatus.SUCCEEDED
            execution_state.consecutive_no_progress = 0
        elif outcome["status"] == "stale":
            node.status = NodeStatus.FAILED
            execution_state.consecutive_no_model_activity += 1
            execution_state.consecutive_no_progress += 1
        else:
            node.status = NodeStatus.FAILED
            execution_state.consecutive_no_progress += 1

        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "node_evaluated", "node_id": node.node_id, "status": node.status.value, "outcome": outcome})
        return outcome

    def _handle_recovery(self, execution_state: MissionExecutionState, node: Any, outcome: dict[str, Any]) -> None:
        outcome["localization_weak"] = node.phase.value == "localize" and node.confidence < 0.55
        decision = self.recovery.plan_recovery(execution_state, node, outcome)
        if decision.mark_blocked:
            node.status = NodeStatus.BLOCKED
            node.blockers.append(decision.reason)
        elif decision.mark_exhausted:
            node.status = NodeStatus.EXHAUSTED
        else:
            self.planner.expand_mission_graph(execution_state.mission, decision.nodes)
        append_mission_event(str(self.repo), execution_state.mission.mission_id, {"type": "recovery", "node_id": node.node_id, "strategy": decision.strategy, "reason": decision.reason, "spawned_nodes": [n.node_id for n in decision.nodes]})

    def _mission_done(self, execution_state: MissionExecutionState):
        outcome, reason = evaluate_mission_status(execution_state)
        if outcome is not None:
            execution_state.mission.final_outcome = outcome.value
            execution_state.mission.stop_reason = reason
            execution_state.mission.state = "finished"
            return outcome
        outcome2, reason2 = evaluate_mission_stop(execution_state)
        if outcome2 is not None:
            execution_state.mission.final_outcome = outcome2.value
            execution_state.mission.stop_reason = reason2
            execution_state.mission.state = "finished"
            return outcome2
        return None

    def _finalize_mission(self, execution_state: MissionExecutionState) -> dict[str, Any]:
        mission = execution_state.mission
        touched = sorted(set(self._git_changed_files()) - set(execution_state.changed_files_baseline))
        report = build_mission_summary(
            mission,
            execution_state,
            files_touched=touched,
            outcome=mission.final_outcome or "exhausted",
            stop_reason=mission.stop_reason or "Mission ended without explicit stop reason.",
        )
        save_final_mission_report(str(self.repo), mission, report)
        append_mission_event(str(self.repo), mission.mission_id, {"type": "mission_finalized", "outcome": mission.final_outcome, "stop_reason": mission.stop_reason})
        text = self.format_summary(report)
        return {
            "mission": mission.to_dict(),
            "report": report,
            "response": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }

    def _node_by_id(self, mission: Mission, node_id: str):
        for n in mission.nodes:
            if n.node_id == node_id:
                return n
        return None

    def _git_changed_files(self) -> list[str]:
        proc = subprocess.run(["git", "diff", "--name-only"], cwd=self.repo, capture_output=True, text=True)
        if proc.returncode != 0:
            return []
        return [x.strip() for x in proc.stdout.splitlines() if x.strip()]

    @staticmethod
    def format_summary(summary: dict[str, Any]) -> str:
        report = summary.get("report", summary)
        lines = [
            "Villani mode mission report",
            f"- Mission: {report.get('mission_goal', '')}",
            f"- Type: {report.get('mission_type', '')}",
            f"- Outcome: {report.get('final_outcome', '')}",
            f"- Stop reason: {report.get('stop_reason', '')}",
            f"- Files touched: {', '.join(report.get('files_touched', [])[:12]) or 'none'}",
            "- Node results:",
        ]
        for node in report.get("nodes_executed", [])[:30]:
            lines.append(f"  * {node.get('title')} [{node.get('status')}] attempts={node.get('attempts')}")
        return "\n".join(lines)
