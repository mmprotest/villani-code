from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.autonomy import Opportunity, TaskContract


def build_wave_candidates(controller: Any, discovered: list[Opportunity]) -> list[Opportunity]:
    combined = list(discovered) + list(controller._retryable_queue) + list(controller._followup_queue)
    dedup: dict[str, Opportunity] = {}
    for op in combined:
        key = controller._task_key_for_opportunity(op)
        if controller._is_task_satisfied(key):
            controller._log(f"[villani-mode] selector skipped satisfied task: {op.title}")
            continue
        if key in controller._stale_task_keys:
            continue
        if controller._is_terminal_lineage(key):
            continue
        if op.confidence < controller.takeover_config.min_confidence:
            continue
        existing = dedup.get(key)
        if existing is None or effective_priority(op) > effective_priority(existing):
            dedup[key] = op
    return sorted(dedup.values(), key=effective_priority, reverse=True)


def effective_priority(op: Opportunity) -> float:
    score = op.priority * 0.7 + op.confidence * 0.3
    if op.title == "Run baseline tests":
        score += 0.45
    if op.title == "Validate CLI entrypoint":
        score += 0.35
    if op.title == "Validate documented commands/examples":
        score += 0.3
    if op.category == "followup_validation":
        score += 0.3
    elif op.category.startswith("followup_"):
        score += 0.2
    return score


def task_key_for_opportunity(op: Opportunity) -> str:
    title = op.title.lower()
    aliases = {
        "re-run baseline importability validation": "validate baseline importability",
        "complete minimal test bootstrap": "bootstrap minimal tests",
        "validate recent autonomous changes": "validate recent autonomous changes",
    }
    normalized = aliases.get(title, title)
    return normalized.replace(" ", "-")


def parent_task_key(op: Opportunity) -> str:
    if op.category.startswith("followup_"):
        return task_key_for_opportunity(op)
    return ""


def retry_limit_for_contract(contract: str) -> int:
    if contract == TaskContract.VALIDATION.value:
        return 2
    return 1


def is_terminal_lineage(controller: Any, task_key: str) -> bool:
    return controller._lineage_status.get(task_key) in {"passed", "blocked", "exhausted"}


def is_actionable_failure(task: Any) -> bool:
    return any([
        bool(task.intentional_changes),
        bool(task.validation_artifacts),
        bool(task.runner_failures),
        bool(task.produced_inspection_conclusion),
    ])


def has_pending_actionable_work(controller: Any) -> bool:
    return any(status == "retryable" for status in controller._lineage_status.values())


def has_any_evidence(task: Any) -> bool:
    return task.produced_effect or task.produced_validation or task.produced_inspection_conclusion


def meets_contract(task: Any) -> bool:
    if task.task_contract == TaskContract.EFFECTFUL.value:
        return task.produced_effect and meets_effectful_minimum(task)
    if task.task_contract == TaskContract.VALIDATION.value:
        return task.produced_validation and meets_validation_minimum(task)
    return has_any_evidence(task)


def meets_effectful_minimum(task: Any) -> bool:
    if task.title == "Bootstrap minimal tests":
        return any(is_test_file(path) for path in task.intentional_changes)
    if task.title == "Audit missing usage docs":
        return any(path.endswith(".md") for path in task.intentional_changes)
    return True


def meets_validation_minimum(task: Any) -> bool:
    if task.title == "Validate baseline importability":
        return has_real_validation_artifact(task)
    return task.produced_validation and has_real_validation_artifact(task)


def has_real_validation_artifact(task: Any) -> bool:
    for artifact in task.validation_artifacts:
        text = str(artifact).strip()
        if not text:
            continue
        if "(exit=0)" not in text.lower():
            continue
        command = text.rsplit("(exit=", 1)[0].strip()
        if command:
            return True
    return False


def is_test_file(path: str) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    name = Path(norm).name
    return (norm.startswith("tests/") and norm.endswith(".py")) or (name.startswith("test_") and name.endswith(".py"))
