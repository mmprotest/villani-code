from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ObservableKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    COMMAND_RESULT = "command_result"
    SERVICE_RESPONSE = "service_response"
    VALIDATION_ARTIFACT = "validation_artifact"
    DIFF = "diff"
    INSPECTION_SUMMARY = "inspection_summary"


@dataclass(slots=True)
class RequiredObservable:
    kind: str
    path: str
    description: str
    must_exist: bool = True
    evidence_command: str = ""
    source: str = "inferred"


@dataclass(slots=True)
class BehavioralCheck:
    description: str
    command: str = ""
    required: bool = True


@dataclass(slots=True)
class TaskOutcomeContract:
    objective: str
    task_mode: str
    success_predicate: str
    preferred_targets: list[str] = field(default_factory=list)
    required_observables: list[RequiredObservable] = field(default_factory=list)
    behavioral_checks: list[BehavioralCheck] = field(default_factory=list)
    no_go_paths: list[str] = field(default_factory=list)
    confidence: float = 0.5
    source: str = "inferred"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TaskOutcomeContract":
        return cls(
            objective=str(payload.get("objective", "")),
            task_mode=str(payload.get("task_mode", "")),
            success_predicate=str(payload.get("success_predicate", "")),
            preferred_targets=[str(v) for v in payload.get("preferred_targets", [])],
            required_observables=[RequiredObservable(**item) for item in payload.get("required_observables", [])],
            behavioral_checks=[BehavioralCheck(**item) for item in payload.get("behavioral_checks", [])],
            no_go_paths=[str(v) for v in payload.get("no_go_paths", [])],
            confidence=float(payload.get("confidence", 0.5)),
            source=str(payload.get("source", "inferred")),
        )


@dataclass(slots=True)
class ContractCheckFinding:
    category: str
    message: str
    path: str = ""
    severity: str = "medium"


@dataclass(slots=True)
class ContractCheckResult:
    satisfied: bool
    findings: list[ContractCheckFinding]
    checked_observables: list[str]
    checked_behavioral_checks: list[str]
    summary: str


def check_contract_satisfaction(
    repo: Path,
    contract: TaskOutcomeContract,
    changed_files: list[str],
    validation_artifacts: list[str],
) -> ContractCheckResult:
    findings: list[ContractCheckFinding] = []
    checked_observables: list[str] = []
    checked_behavioral_checks: list[str] = []

    normalized_changed = {_normalize_path(path) for path in changed_files}
    artifact_text = "\n".join(str(item) for item in validation_artifacts)

    for observable in contract.required_observables:
        kind = str(observable.kind)
        target_path = _normalize_path(observable.path)
        checked_observables.append(f"{kind}:{target_path}")
        absolute_path = repo / target_path

        satisfied = True
        if kind == ObservableKind.FILE.value:
            satisfied = absolute_path.exists() and absolute_path.is_file()
        elif kind == ObservableKind.DIRECTORY.value:
            satisfied = absolute_path.exists() and absolute_path.is_dir()
        elif kind == ObservableKind.VALIDATION_ARTIFACT.value:
            satisfied = bool(target_path) and target_path in artifact_text
        elif kind == ObservableKind.DIFF.value:
            satisfied = bool(target_path) and (
                target_path in normalized_changed
                or any(path.startswith(f"{target_path}/") for path in normalized_changed)
            )

        if not satisfied:
            findings.append(
                ContractCheckFinding(
                    category="required_observable",
                    message=f"Required observable not satisfied: {kind} {target_path}",
                    path=target_path,
                    severity="high",
                )
            )

    for check in contract.behavioral_checks:
        descriptor = check.command.strip() or check.description.strip()
        if not descriptor:
            continue
        checked_behavioral_checks.append(descriptor)
        if check.command.strip() and check.command.strip() not in artifact_text:
            findings.append(
                ContractCheckFinding(
                    category="behavioral_check",
                    message=f"Required behavioral check has no supporting evidence: {check.command.strip()}",
                    path="",
                    severity="high",
                )
            )

    if not contract.required_observables and not contract.behavioral_checks:
        return ContractCheckResult(
            satisfied=True,
            findings=[],
            checked_observables=[],
            checked_behavioral_checks=[],
            summary="Contract has no required observables or behavioral checks.",
        )

    satisfied = not findings
    summary = "Contract satisfied." if satisfied else f"Contract unsatisfied with {len(findings)} finding(s)."
    return ContractCheckResult(
        satisfied=satisfied,
        findings=findings,
        checked_observables=checked_observables,
        checked_behavioral_checks=checked_behavioral_checks,
        summary=summary,
    )


def _normalize_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().lstrip("./")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = _normalize_path(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _extract_instruction_paths(instruction: str) -> list[str]:
    pattern = re.compile(r"(?:[\w.-]+/)+[\w.-]+|[\w.-]+\.[A-Za-z0-9_]+")
    matches = [_normalize_path(m.group(0)) for m in pattern.finditer(instruction or "")]
    return _dedupe(matches)


def build_task_outcome_contract(
    repo: Path,
    instruction: str,
    task_mode: Any,
    execution_plan: Any | None = None,
    benchmark_config: Any | None = None,
    existing_preferred_targets: list[str] | None = None,
) -> TaskOutcomeContract:
    del repo  # reserved for future heuristics
    instruction_paths = _extract_instruction_paths(instruction)
    plan_files = [str(v) for v in list(getattr(execution_plan, "relevant_files", []) or [])]
    validation_steps = [str(v) for v in list(getattr(execution_plan, "validation_steps", []) or []) if str(v).strip()]
    runtime_expected = [str(v) for v in list(getattr(benchmark_config, "expected_files", []) or [])]

    preferred_targets = _dedupe(
        list(existing_preferred_targets or []) + plan_files + instruction_paths
    )

    required_observables: list[RequiredObservable] = []
    for path in instruction_paths:
        if "." in Path(path).name:
            kind = ObservableKind.FILE.value
        else:
            kind = ObservableKind.DIRECTORY.value
        required_observables.append(
            RequiredObservable(
                kind=kind,
                path=path,
                description=f"Instruction-referenced target: {path}",
                must_exist=True,
                source="instruction",
            )
        )

    for path in _dedupe(runtime_expected):
        required_observables.append(
            RequiredObservable(
                kind=ObservableKind.FILE.value,
                path=path,
                description=f"Runtime-expected artifact: {path}",
                must_exist=True,
                source="runtime_config",
            )
        )

    behavioral_checks = [
        BehavioralCheck(description=f"Run validation step: {step}", command=step, required=True)
        for step in validation_steps
    ]

    mode_value = getattr(task_mode, "value", task_mode)
    mode_text = str(mode_value or "")
    success = "Required observables exist and required behavioral checks succeed."
    if not required_observables and not behavioral_checks:
        success = "Task objective is addressed with observable, auditable evidence."

    return TaskOutcomeContract(
        objective=str(instruction or "").strip(),
        task_mode=mode_text,
        success_predicate=success,
        preferred_targets=preferred_targets,
        required_observables=required_observables,
        behavioral_checks=behavioral_checks,
        no_go_paths=[],
        confidence=0.5,
        source="inferred",
    )


def format_contract_for_model(contract: TaskOutcomeContract) -> str:
    objective = contract.objective.strip() or "unspecified objective"
    success_predicate = contract.success_predicate.strip() or "Provide auditable evidence of progress."
    preferred_targets = [target.strip() for target in contract.preferred_targets if target.strip()][:4]
    required_observables = contract.required_observables[:6]
    behavioral_checks = [check.description.strip() for check in contract.behavioral_checks if check.description.strip()][:6]

    lines = [
        "<task_outcome_contract>",
        f"objective: {objective}",
        f"success_predicate: {success_predicate}",
        "preferred_targets:",
        *([f"- {target}" for target in preferred_targets] or ["- none"]),
        "required_observables:",
        *(
            [
                f"- kind={obs.kind} path={obs.path} description={obs.description}"
                for obs in required_observables
            ]
            or ["- none"]
        ),
        "behavioral_checks:",
        *([f"- {description}" for description in behavioral_checks] or ["- none"]),
        "completion_rule: Claim completion after required observables and behavioral checks have supporting evidence.",
        "</task_outcome_contract>",
    ]
    return "\n".join(lines)
