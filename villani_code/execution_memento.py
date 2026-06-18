from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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

    strongest_evidence: list[str]
    current_hypothesis: str
    rejected_hypotheses: list[str]

    in_scope_files: list[str]
    out_of_scope_files: list[str]
    changed_files: list[str]

    last_action: str
    last_verification_result: str
    unresolved_blocker: str

    next_best_action: str
    next_action_reason: str

    pinned_constraints: list[str]
    success_predicate: str

    turn_index: int
    updated_at: str


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def build_execution_memento(runner: "Runner") -> ExecutionMemento:
    mission = getattr(runner, "_mission_state", None)
    contract = getattr(runner, "_task_contract", {}) or {}
    objective = str(getattr(mission, "objective", "") or "").strip()
    success_predicate = str(contract.get("success_predicate", "")).strip()

    verified = [str(getattr(fact, "value", "")).strip() for fact in getattr(mission, "verified_facts", [])]
    validation_failures = [str(v).strip() for v in getattr(mission, "validation_failures", [])]
    strongest_evidence = _dedupe(
        verified
        + validation_failures
        + [str(getattr(runner, "_last_validation_summary", "")).strip()]
        + [str(getattr(mission, "last_failed_summary", "")).strip()],
        limit=5,
    )

    open_hypotheses = list(getattr(mission, "open_hypotheses", []))
    current_hypothesis = ""
    rejected: list[str] = []
    for hypothesis in open_hypotheses:
        statement = str(getattr(hypothesis, "statement", "")).strip()
        status = str(getattr(hypothesis, "status", "")).strip().lower()
        if not statement:
            continue
        if status in {"open", "active", "current"} and not current_hypothesis:
            current_hypothesis = statement
        elif status in {"rejected", "invalid", "discarded"}:
            rejected.append(statement)
    if not current_hypothesis:
        current_hypothesis = (
            "A focused patch in current scope should satisfy the success predicate."
            if success_predicate
            else "Current scoped fix should resolve the task objective."
        )

    plan_files = [str(path) for path in getattr(getattr(runner, "_execution_plan", None), "relevant_files", [])]
    in_scope = _dedupe(
        _filter_model_facing_paths(list(getattr(mission, "intended_targets", [])) + plan_files),
        limit=8,
    )
    changed = _dedupe(_filter_model_facing_paths(list(getattr(mission, "changed_files", []))), limit=8)

    no_go_paths = [str(v).strip() for v in contract.get("no_go_paths", []) if str(v).strip()]
    out_of_scope = _dedupe(_filter_model_facing_paths(no_go_paths), limit=8)

    last_verification_result = str(getattr(runner, "_last_validation_summary", "")).strip()
    unresolved_blocker = str(getattr(mission, "last_failed_summary", "")).strip()
    if not unresolved_blocker and validation_failures:
        unresolved_blocker = validation_failures[0]

    next_best_action = ""
    next_action_reason = ""
    pending_verification = str(getattr(runner, "_pending_verification", "")).strip()
    if pending_verification:
        next_best_action = "Review latest verification output and apply one bounded repair."
        next_action_reason = "Pending verification produced fresh failure evidence."
    elif unresolved_blocker:
        next_best_action = "Address unresolved blocker in the highest-signal in-scope file."
        next_action_reason = "Latest run is still blocked."
    else:
        next_best_action = "Run verification against intended targets."
        next_action_reason = "Need confirmation against the success predicate."

    pinned_constraints = _dedupe(
        [f"Success predicate: {success_predicate}" if success_predicate else ""]
        + [f"No-go: {path}" for path in no_go_paths[:4]]
        + ["Prefer surgical patch."]
        + ([f"Stay within preferred targets: {', '.join(contract.get('preferred_targets', [])[:4])}"] if contract.get("preferred_targets") else []),
        limit=8,
    )

    return ExecutionMemento(
        objective=objective,
        current_subgoal=str(getattr(mission, "plan_summary", "")).strip() or objective,
        current_step_id=str(getattr(mission, "current_step_id", "")).strip() or "localization",
        strongest_evidence=strongest_evidence,
        current_hypothesis=current_hypothesis,
        rejected_hypotheses=_dedupe(rejected, limit=5),
        in_scope_files=in_scope,
        out_of_scope_files=out_of_scope,
        changed_files=changed,
        last_action=str(getattr(mission, "last_failed_command", "")).strip() or "Updated execution state.",
        last_verification_result=last_verification_result or "No verification result recorded.",
        unresolved_blocker=unresolved_blocker,
        next_best_action=next_best_action,
        next_action_reason=next_action_reason,
        pinned_constraints=pinned_constraints,
        success_predicate=success_predicate,
        turn_index=int(getattr(runner, "_current_turn_index", 0) or 0),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_execution_memento(repo: Path, mission_id: str, memento: ExecutionMemento) -> Path:
    mission_dir = get_mission_dir(repo.resolve(), mission_id)
    ensure_dir(mission_dir)
    json_path = mission_dir / "execution_memento.json"
    payload = asdict(memento)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (mission_dir / "execution_memento.md").write_text(render_execution_memento_for_model(memento) + "\n", encoding="utf-8")
    return json_path


def load_execution_memento(repo: Path, mission_id: str) -> ExecutionMemento | None:
    path = get_mission_dir(repo.resolve(), mission_id) / "execution_memento.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ExecutionMemento(**payload)


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
        "Out of scope:",
        *[f"- {line}" for line in memento.out_of_scope_files[:8]],
        "Changed:",
        *[f"- {line}" for line in memento.changed_files[:8]],
        f"Last action: {memento.last_action}",
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
    lines = ["LOCAL EVIDENCE"]
    last_validation = str(getattr(runner, "_last_validation_summary", "")).strip()
    if last_validation:
        lines.append(f"- verification: {last_validation}")
    last_failed = str(getattr(getattr(runner, "_mission_state", None), "last_failed_summary", "")).strip()
    if last_failed:
        lines.append(f"- last failure: {last_failed[:180]}")
    pending = str(getattr(runner, "_pending_verification", "")).strip()
    if pending:
        compact = " ".join(pending.splitlines())
        lines.append(f"- pending verification: {compact[:220]}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
