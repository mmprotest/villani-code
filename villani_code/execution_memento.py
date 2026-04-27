from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from villani_code.context_projection import _filter_model_facing_paths
from villani_code.mission_state import get_mission_dir
from villani_code.utils import ensure_dir

if TYPE_CHECKING:
    from villani_code.state import Runner


@dataclass(slots=True)
class ExecutionMemento:
    objective: str
    current_subgoal: str
    current_step_id: str

    strongest_evidence: list[str] = field(default_factory=list)
    current_hypothesis: str = ""
    rejected_hypotheses: list[str] = field(default_factory=list)

    in_scope_files: list[str] = field(default_factory=list)
    out_of_scope_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)

    last_action: str = ""
    last_verification_result: str = ""
    unresolved_blocker: str = ""

    next_best_action: str = ""
    next_action_reason: str = ""

    pinned_constraints: list[str] = field(default_factory=list)
    success_predicate: str = ""

    turn_index: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ExecutionMemento":
        return cls(
            objective=str(payload.get("objective", "")),
            current_subgoal=str(payload.get("current_subgoal", "")),
            current_step_id=str(payload.get("current_step_id", "")),
            strongest_evidence=[str(v) for v in payload.get("strongest_evidence", [])][:5],
            current_hypothesis=str(payload.get("current_hypothesis", "")),
            rejected_hypotheses=[str(v) for v in payload.get("rejected_hypotheses", [])][:5],
            in_scope_files=[str(v) for v in payload.get("in_scope_files", [])][:8],
            out_of_scope_files=[str(v) for v in payload.get("out_of_scope_files", [])][:8],
            changed_files=[str(v) for v in payload.get("changed_files", [])][:8],
            last_action=str(payload.get("last_action", "")),
            last_verification_result=str(payload.get("last_verification_result", "")),
            unresolved_blocker=str(payload.get("unresolved_blocker", "")),
            next_best_action=str(payload.get("next_best_action", "")),
            next_action_reason=str(payload.get("next_action_reason", "")),
            pinned_constraints=[str(v) for v in payload.get("pinned_constraints", [])],
            success_predicate=str(payload.get("success_predicate", "")),
            turn_index=int(payload.get("turn_index", 0) or 0),
            updated_at=str(payload.get("updated_at", "")),
        )


def _normalize_items(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def build_execution_memento(runner: "Runner") -> ExecutionMemento:
    mission = getattr(runner, "_mission_state", None)
    contract = getattr(runner, "_task_contract", {}) or {}
    verified = [str(f.value) for f in getattr(mission, "verified_facts", [])]
    open_hyp = [str(h.statement) for h in getattr(mission, "open_hypotheses", []) if str(h.status).lower() != "rejected"]
    rejected_hyp = [str(h.statement) for h in getattr(mission, "open_hypotheses", []) if str(h.status).lower() == "rejected"]

    validation_failures = [str(v) for v in getattr(mission, "validation_failures", [])]
    last_failed_summary = str(getattr(mission, "last_failed_summary", "") or "").strip()
    last_failed_command = str(getattr(mission, "last_failed_command", "") or "").strip()
    last_validation_summary = str(getattr(runner, "_last_validation_summary", "") or "").strip()

    evidence: list[str] = []
    evidence.extend(verified[:3])
    if last_validation_summary:
        evidence.append(f"latest verification summary: {last_validation_summary}")
    if validation_failures:
        evidence.append(f"validation failure: {validation_failures[0]}")
    if last_failed_command:
        evidence.append(f"failed command: {last_failed_command}")

    intended_targets = _filter_model_facing_paths(list(getattr(mission, "intended_targets", []))) if mission else []
    changed_files = _filter_model_facing_paths(list(getattr(mission, "changed_files", []))) if mission else []

    no_go = [str(v) for v in contract.get("no_go_paths", []) if str(v).strip()]
    constraints = [
        str(v)
        for v in [
            f"task mode: {contract.get('task_mode', '')}" if contract.get("task_mode") else "",
            f"risk: {contract.get('risk', '')}" if contract.get("risk") else "",
        ]
        if str(v).strip()
    ]
    constraints.extend(f"no-go: {v}" for v in no_go[:4])
    if not constraints:
        constraints = ["prefer surgical patch", "stay within intended target files"]

    objective = str(getattr(mission, "objective", "") or "").strip()
    success_predicate = str(contract.get("success_predicate", "") or "").strip()
    if not success_predicate:
        success_predicate = "deliver a bounded patch with verification signal"

    current_hypothesis = open_hyp[0] if open_hyp else (last_failed_summary or "current issue is localized to intended targets")
    unresolved = ""
    if validation_failures:
        unresolved = validation_failures[0]
    elif last_failed_summary:
        unresolved = last_failed_summary

    step = str(getattr(mission, "current_step_id", "") or "").strip()
    if not step:
        step = "verification" if (last_validation_summary or validation_failures) else "patching"

    next_action = "inspect latest failing signal in intended target and patch minimally"
    if validation_failures:
        next_action = "repair failing validation in current scope"
    elif not changed_files and intended_targets:
        next_action = f"apply first minimal patch in {intended_targets[0]}"

    turn_index = int(getattr(runner, "_current_turn_index", 0) or 0)
    return ExecutionMemento(
        objective=objective,
        current_subgoal=str(getattr(mission, "plan_summary", "") or "").strip() or "advance the active mission step",
        current_step_id=step,
        strongest_evidence=_normalize_items(evidence, 5),
        current_hypothesis=current_hypothesis,
        rejected_hypotheses=_normalize_items(rejected_hyp, 5),
        in_scope_files=_normalize_items(intended_targets, 8),
        out_of_scope_files=_normalize_items(no_go, 8),
        changed_files=_normalize_items(changed_files, 8),
        last_action=(last_failed_command or "updated mission state")[:200],
        last_verification_result=(last_validation_summary or (validation_failures[0] if validation_failures else "verification not yet run"))[:220],
        unresolved_blocker=unresolved[:220],
        next_best_action=next_action,
        next_action_reason="it is the shortest path to satisfy the success predicate with bounded scope",
        pinned_constraints=_normalize_items(constraints, 8),
        success_predicate=success_predicate,
        turn_index=turn_index,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_execution_memento(repo: Path, mission_id: str, memento: ExecutionMemento) -> Path:
    mission_dir = get_mission_dir(repo.resolve(), mission_id)
    ensure_dir(mission_dir)
    path = mission_dir / "execution_memento.json"
    path.write_text(json.dumps(memento.to_dict(), indent=2), encoding="utf-8")
    md = mission_dir / "execution_memento.md"
    md.write_text(render_execution_memento_for_model(memento) + "\n", encoding="utf-8")
    return path


def load_execution_memento(repo: Path, mission_id: str) -> ExecutionMemento | None:
    path = get_mission_dir(repo.resolve(), mission_id) / "execution_memento.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return ExecutionMemento.from_dict(payload)


def render_execution_memento_for_model(memento: ExecutionMemento) -> str:
    lines = [
        "EXECUTION STATE",
        f"Objective: {memento.objective}",
        f"Subgoal: {memento.current_subgoal}",
        f"Step: {memento.current_step_id}",
        f"Hypothesis: {memento.current_hypothesis}",
        "Evidence:",
        *[f"- {line}" for line in memento.strongest_evidence[:5]],
        "Rejected:",
        *[f"- {line}" for line in memento.rejected_hypotheses[:5]],
        "In scope:",
        *[f"- {line}" for line in memento.in_scope_files[:8]],
        "Changed:",
        *[f"- {line}" for line in memento.changed_files[:8]],
        f"Last verification: {memento.last_verification_result}",
        f"Blocker: {memento.unresolved_blocker}",
        f"Next action: {memento.next_best_action}",
        f"Why next: {memento.next_action_reason}",
        "Constraints:",
        *[f"- {line}" for line in memento.pinned_constraints[:8]],
        f"Success: {memento.success_predicate}",
    ]
    return "\n".join(lines)


def build_local_evidence_block(runner: "Runner") -> str:
    items: list[str] = []
    target = str(getattr(runner, "_last_validation_target", "") or "").strip()
    summary = str(getattr(runner, "_last_validation_summary", "") or "").strip()
    pending = str(getattr(runner, "_pending_verification", "") or "").strip()
    mission = getattr(runner, "_mission_state", None)
    if target:
        items.append(f"last_validation_target: {target}")
    if summary:
        items.append(f"last_validation_summary: {summary}")
    if pending:
        items.append(f"pending_verification: {pending.splitlines()[0][:180]}")
    if mission is not None and getattr(mission, "last_failed_command", ""):
        items.append(f"last_failed_command: {mission.last_failed_command}")
    if mission is not None and getattr(mission, "last_failed_summary", ""):
        items.append(f"last_failed_summary: {str(mission.last_failed_summary)[:180]}")
    if not items:
        return ""
    return "LOCAL EVIDENCE\n" + "\n".join(f"- {line}" for line in items[:4])


def build_fallback_execution_state_block(runner: "Runner") -> str:
    mission = getattr(runner, "_mission_state", None)
    contract = getattr(runner, "_task_contract", {}) or {}

    objective = str(getattr(mission, "objective", "") or "").strip() or "Continue the active repair mission"
    success = str(contract.get("success_predicate", "") or "").strip()
    if not success:
        success = "Complete the requested task and make verification pass"

    validation_failures = [str(v).strip() for v in getattr(mission, "validation_failures", []) if str(v).strip()]
    last_validation_summary = str(getattr(runner, "_last_validation_summary", "") or "").strip()
    last_failed_summary = str(getattr(mission, "last_failed_summary", "") or "").strip()
    if last_validation_summary:
        current_verification = last_validation_summary
    elif validation_failures:
        current_verification = validation_failures[0]
    elif last_failed_summary:
        current_verification = last_failed_summary
    else:
        current_verification = "verification still pending"

    changed_files = list(getattr(mission, "changed_files", []) if mission is not None else [])
    intended_targets = list(getattr(mission, "intended_targets", []) if mission is not None else [])
    if validation_failures or any(token in current_verification.lower() for token in ("fail", "error", "uncertain")):
        next_action = "Fix the remaining failing verification and rerun tests"
    elif changed_files and "pass" not in current_verification.lower():
        next_action = "Run verification to confirm the fix"
    elif intended_targets:
        next_action = "Inspect and repair the most likely target file"
    else:
        next_action = "Identify the failing component and continue repair"

    pinned_constraints = [str(v).strip() for v in contract.get("no_go_paths", []) if str(v).strip()]
    task_mode = str(contract.get("task_mode", "") or "").strip()
    if task_mode:
        pinned_constraints.insert(0, f"task mode: {task_mode}")
    constraints_text = ", ".join(pinned_constraints[:4]) if pinned_constraints else "prefer surgical patch"

    lines = [
        "EXECUTION STATE",
        f"Objective: {objective}",
        f"Success: {success}",
        f"Current verification: {current_verification[:220]}",
        f"Next action: {next_action}",
        f"Constraints: {constraints_text}",
    ]
    return "\n".join(lines)
