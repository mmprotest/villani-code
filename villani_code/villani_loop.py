from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from villani_code.execution import ExecutionBudget
from villani_code.villani_actions import ActionKind, VillaniAction, choose_best_action, propose_actions
from villani_code.villani_cleanup import cleanup_candidates
from villani_code.villani_observe import observe_workspace, update_beliefs
from villani_code.villani_state import (
    ActionResultSummary,
    FailureObservation,
    WorkspaceBeliefState,
    load_beliefs,
    save_beliefs,
)
from villani_code.villani_stop import should_stop


@dataclass(slots=True)
class VillaniLoopConfig:
    max_iterations: int = 8
    action_budget: ExecutionBudget = field(
        default_factory=lambda: ExecutionBudget(
            max_turns=6,
            max_tool_calls=20,
            max_seconds=120.0,
            max_no_edit_turns=4,
            max_reconsecutive_recon_turns=3,
        )
    )


def detect_loop_signals(beliefs: WorkspaceBeliefState) -> list[str]:
    signals: list[str] = []
    recent = beliefs.action_history[-4:]
    if len(recent) >= 3 and len({r.action_kind for r in recent}) == 1:
        signals.append(f"repeated_action:{recent[-1].action_kind}")
    recent_failures = [tuple(f.signature for f in r.failures) for r in recent if r.failures]
    if len(recent_failures) >= 2 and len(set(recent_failures)) == 1:
        signals.append("stable_failure_signature")
    if len(recent) >= 4 and all(not r.changed_files for r in recent):
        signals.append("no_meaningful_changes")
    if beliefs.validated_artifacts and any(
        r.action_kind == ActionKind.IMPLEMENT.value and not r.changed_files for r in recent[-3:]
    ):
        signals.append("repeated_implement_after_validation")
    if len(recent) >= 3 and all(r.action_kind == ActionKind.SUMMARIZE.value for r in recent[-3:]):
        signals.append("repeated_summaries")
    scratch_only = [
        r
        for r in recent[-3:]
        if r.changed_files and all(ch in beliefs.scratch_artifacts for ch in r.changed_files)
    ]
    if len(scratch_only) >= 2:
        signals.append("scratch_only_progress")
    return signals


def _build_action_prompt(beliefs: WorkspaceBeliefState, action: VillaniAction) -> str:
    return (
        "Villani autonomous action.\n"
        f"Objective: {beliefs.objective}\n"
        f"Workspace: {beliefs.workspace_summary}\n"
        f"Deliverables: {beliefs.likely_deliverables[:8]}\n"
        f"Known failures: {[f.signature for f in beliefs.known_failures[:5]]}\n"
        f"Chosen action: {action.kind.value} - {action.intent}\n"
        f"Expected evidence: {action.expected_evidence}\n"
        "Use tools for real evidence. Do not invent state or claim validation without command output."
    )


def _result_summary(action: VillaniAction, result: dict[str, Any], beliefs: WorkspaceBeliefState) -> ActionResultSummary:
    execution = result.get("execution", {})
    changed = execution.get("intentional_changes", execution.get("files_changed", []))
    failures = list(beliefs.known_failures)
    return ActionResultSummary(
        action_kind=action.kind.value,
        success=not failures,
        changed_files=[c for c in changed if c not in beliefs.scratch_artifacts],
        validation_observations=list(beliefs.validation_observations),
        failures=failures,
        notes=str(result.get("response", {}))[:240],
    )


def _escape_action(beliefs: WorkspaceBeliefState) -> VillaniAction:
    if beliefs.unresolved_critical_issues:
        return VillaniAction(
            kind=ActionKind.REPAIR,
            intent="Break loop by targeting stable failure signature",
            rationale="Loop detected around unresolved failure.",
            expected_evidence=["different failure signature or pass"],
            priority=1.0,
            confidence=0.9,
            risk="high",
        )
    return VillaniAction(
        kind=ActionKind.STOP,
        intent="Stop cleanly due to no new learning",
        rationale="Loop detected and no critical issues remain.",
        expected_evidence=["no useful delta from prior actions"],
        priority=1.0,
        confidence=0.9,
        risk="low",
    )


def run_villani_loop(
    runner: Any,
    repo: Path,
    objective: str,
    event_callback: Any | None = None,
    config: VillaniLoopConfig | None = None,
) -> dict[str, Any]:
    config = config or VillaniLoopConfig()
    event_callback = event_callback or (lambda _e: None)
    beliefs = load_beliefs(repo, objective) or observe_workspace(repo, objective)

    iterations = 0
    stop_reason = "Budget exhausted."
    while iterations < config.max_iterations:
        iterations += 1
        event_callback({"type": "autonomous_phase", "phase": f"villani-loop-{iterations}"})
        beliefs = update_beliefs(beliefs, observe_workspace(repo, objective, None))
        decision = should_stop(beliefs)
        if decision.should_stop:
            stop_reason = decision.reason
            break

        candidates = propose_actions(beliefs)
        action = choose_best_action(candidates)

        loop_signals = detect_loop_signals(beliefs)
        if loop_signals:
            beliefs.repeated_patterns = loop_signals
            action = _escape_action(beliefs)
            if action.kind == ActionKind.STOP:
                stop_reason = "Loop detected with no unresolved critical failures."
                break

        if action.kind == ActionKind.CLEANUP:
            for rel in cleanup_candidates(beliefs.scratch_artifacts):
                target = repo / rel
                if target.exists() and target.is_file():
                    target.unlink()
            beliefs.add_action_result(ActionResultSummary(action_kind="cleanup", success=True, notes="deleted scratch files"))
            continue

        if action.kind == ActionKind.STOP:
            stop_reason = "Action policy selected stop."
            break

        run_result = runner.run_villani_action(
            objective=objective,
            belief_state=beliefs.to_snapshot(),
            chosen_action=asdict(action),
            expected_evidence=list(action.expected_evidence),
            focus_files=list(action.target_files),
            known_failures=[f.signature for f in beliefs.known_failures[:8]],
            execution_budget=config.action_budget,
        )
        observed = observe_workspace(repo, objective, run_result)
        beliefs = update_beliefs(beliefs, observed)
        summary = _result_summary(action, run_result, beliefs)
        beliefs.add_action_result(summary)
        save_beliefs(repo, beliefs)

    final = {
        "done_reason": stop_reason,
        "iterations": iterations,
        "beliefs": beliefs.to_snapshot(),
        "working_memory": {
            "repeated_patterns": list(beliefs.repeated_patterns),
            "recent_actions": [asdict(a) for a in beliefs.action_history[-5:]],
        },
    }
    event_callback({"type": "autonomous_completed", "done_reason": stop_reason})
    return final


def format_villani_summary(summary: dict[str, Any]) -> str:
    beliefs = summary.get("beliefs", {})
    return (
        "Villani autonomous loop complete.\n"
        f"reason: {summary.get('done_reason', '')}\n"
        f"iterations: {summary.get('iterations', 0)}\n"
        f"confidence: {beliefs.get('completion_confidence', 0)}\n"
        f"deliverables: {beliefs.get('likely_deliverables', [])[:6]}\n"
        f"failures: {beliefs.get('unresolved_critical_issues', [])[:4]}"
    )
