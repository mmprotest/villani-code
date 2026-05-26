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
    EXISTING_FILE = "existing_file"
    MODIFIED_FILE = "modified_file"
    GENERATED_FILE = "generated_file"
    GENERATED_DIRECTORY = "generated_directory"
    VALIDATION_EVIDENCE = "validation_evidence"


@dataclass(slots=True)
class RequiredObservable:
    kind: str
    path: str
    description: str
    must_exist: bool = True
    evidence_command: str = ""
    source: str = "inferred"
    purpose: str = "evidence"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RequiredObservable":
        return cls(
            kind=str(payload.get("kind", "")),
            path=str(payload.get("path", "")),
            description=str(payload.get("description", "")),
            must_exist=bool(payload.get("must_exist", True)),
            evidence_command=str(payload.get("evidence_command", "")),
            source=str(payload.get("source", "inferred")),
            purpose=str(payload.get("purpose", "evidence")),
        )


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
            required_observables=[
                RequiredObservable.from_dict(item) for item in payload.get("required_observables", [])
            ],
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

    def _path_exists(path: Path, observable_kind: str) -> bool:
        if observable_kind in (ObservableKind.DIRECTORY.value, ObservableKind.GENERATED_DIRECTORY.value):
            return path.exists() and path.is_dir()
        return path.exists()

    for observable in contract.required_observables:
        kind = str(observable.kind)
        purpose = str(observable.purpose or "").strip().lower()
        target_path = _normalize_path(observable.path)
        checked_observables.append(f"{kind}:{target_path}")
        absolute_path = repo / target_path

        satisfied = True
        if purpose in {"reference", "must_exist", "must_generate"}:
            satisfied = _path_exists(absolute_path, kind)
        elif purpose == "must_change":
            satisfied = bool(target_path) and (
                target_path in normalized_changed
                or any(path.startswith(f"{target_path}/") for path in normalized_changed)
            )
            if not satisfied and _path_exists(absolute_path, kind):
                findings.append(
                    ContractCheckFinding(
                        category="missing_change_evidence",
                        message=f"Referenced file exists but no modification evidence was found: {target_path}",
                        path=target_path,
                        severity="high",
                    )
                )
                continue
        elif purpose == "must_validate":
            satisfied = bool(target_path) and target_path in artifact_text
        elif kind == ObservableKind.MODIFIED_FILE.value:
            satisfied = bool(target_path) and (
                target_path in normalized_changed
                or any(path.startswith(f"{target_path}/") for path in normalized_changed)
            )
            if not satisfied and _path_exists(absolute_path, kind):
                findings.append(
                    ContractCheckFinding(
                        category="missing_change_evidence",
                        message=f"Referenced file exists but no modification evidence was found: {target_path}",
                        path=target_path,
                        severity="high",
                    )
                )
                continue
        elif kind == ObservableKind.GENERATED_FILE.value:
            satisfied = _path_exists(absolute_path, kind)
        elif kind == ObservableKind.VALIDATION_EVIDENCE.value:
            satisfied = bool(target_path) and target_path in artifact_text
        elif kind == ObservableKind.FILE.value:
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


def _has_context_keyword(context: str, keywords: tuple[str, ...]) -> bool:
    text = f" {context.lower()} "
    return any(f" {keyword} " in text for keyword in keywords)


def classify_instruction_path_mentions(instruction: str) -> list[RequiredObservable]:
    pattern = re.compile(r"(?:[\w.-]+/)+[\w.-]+|[\w.-]+\.[A-Za-z0-9_]+")
    dedupe_seen: set[str] = set()
    observables: list[RequiredObservable] = []

    generate_keywords = (
        "write", "create", "generate", "produce", "output", "save", "export",
        "report", "results", "artifact", "file named", "write to", "save to",
    )
    modify_keywords = (
        "fix", "update", "modify", "edit", "patch", "change", "repair", "refactor", "implement",
    )
    reference_keywords = ("inspect", "read", "check", "look at", "review", "use")

    for match in pattern.finditer(instruction or ""):
        raw_path = match.group(0).strip("'\"`[](){}<>,.;:!?")
        path = _normalize_path(raw_path)
        if not path or path in dedupe_seen:
            continue
        dedupe_seen.add(path)

        start = max(0, match.start() - 120)
        end = min(len(instruction or ""), match.end() + 40)
        context = (instruction or "")[start:end]

        kind = ObservableKind.EXISTING_FILE.value
        purpose = "reference"
        if _has_context_keyword(context, generate_keywords):
            kind = ObservableKind.GENERATED_FILE.value
            purpose = "must_generate"
        elif _has_context_keyword(context, modify_keywords):
            kind = ObservableKind.MODIFIED_FILE.value
            purpose = "must_change"
        elif _has_context_keyword(context, reference_keywords):
            kind = ObservableKind.EXISTING_FILE.value
            purpose = "reference"

        observables.append(
            RequiredObservable(
                kind=kind,
                path=path,
                description=f"Instruction-referenced target: {path}",
                must_exist=True,
                source="instruction",
                purpose=purpose,
            )
        )

    return observables


def build_task_outcome_contract(
    repo: Path,
    instruction: str,
    task_mode: Any,
    execution_plan: Any | None = None,
    benchmark_config: Any | None = None,
    existing_preferred_targets: list[str] | None = None,
) -> TaskOutcomeContract:
    del repo  # reserved for future heuristics
    instruction_observables = classify_instruction_path_mentions(instruction)
    instruction_paths = [obs.path for obs in instruction_observables]
    plan_files = [str(v) for v in list(getattr(execution_plan, "relevant_files", []) or [])]
    validation_steps = [str(v) for v in list(getattr(execution_plan, "validation_steps", []) or []) if str(v).strip()]
    runtime_expected = [str(v) for v in list(getattr(benchmark_config, "expected_files", []) or [])]

    preferred_targets = _dedupe(
        list(existing_preferred_targets or []) + plan_files + instruction_paths
    )

    required_observables: list[RequiredObservable] = list(instruction_observables)

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
        "completion_gate: Claim completion after required observables and behavioral checks have supporting validation evidence for the task outcome contract.",
        "</task_outcome_contract>",
    ]
    return "\n".join(lines)
