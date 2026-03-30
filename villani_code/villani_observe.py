from __future__ import annotations

from pathlib import Path
from typing import Any

from villani_code.evidence import normalize_artifact, parse_command_evidence
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.villani_cleanup import detect_scratch_files
from villani_code.villani_state import FailureObservation, ValidationObservation, WorkspaceBeliefState


SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}


def _iter_repo_files(repo: Path) -> list[str]:
    out: list[str] = []
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(repo).as_posix()
        if is_ignored_repo_path(rel):
            continue
        out.append(rel)
    return sorted(out)


def observe_workspace(repo: Path, objective: str, recent_result: dict[str, Any] | None = None) -> WorkspaceBeliefState:
    files = _iter_repo_files(repo)
    source_files = [f for f in files if Path(f).suffix.lower() in SOURCE_SUFFIXES]
    tests = [f for f in source_files if f.startswith("tests/") or "test" in Path(f).name.lower()]
    docs = [f for f in files if f.lower().endswith((".md", ".rst", ".txt"))]
    entrypoints = [
        f
        for f in source_files
        if Path(f).name.lower() in {"main.py", "app.py", "cli.py", "manage.py"}
    ]
    scratch = detect_scratch_files(files)
    deliverables = [
        f for f in source_files if classify_repo_path(f) == "authoritative" and f not in tests and f not in scratch
    ]

    validations: list[ValidationObservation] = []
    failures: list[FailureObservation] = []
    meaningful_changes: list[str] = []
    if recent_result:
        execution = recent_result.get("execution", {})
        meaningful_changes = [
            p for p in execution.get("intentional_changes", execution.get("files_changed", [])) if p not in scratch
        ]
        for tool_result in recent_result.get("transcript", {}).get("tool_results", []):
            if tool_result.get("is_error"):
                failures.append(
                    FailureObservation(
                        signature=f"tool_error:{str(tool_result.get('content', ''))[:120]}",
                        detail=str(tool_result.get("content", ""))[:300],
                        source="tool",
                    )
                )
            for record in parse_command_evidence(str(tool_result.get("content", ""))):
                artifact = normalize_artifact(record)
                if artifact:
                    validations.append(
                        ValidationObservation(
                            command=str(record.get("command", "")),
                            exit_code=int(record.get("exit", 1)),
                            source="tool_result",
                        )
                    )
        for raw in execution.get("validation_artifacts", []):
            for record in parse_command_evidence(str(raw)):
                validations.append(
                    ValidationObservation(
                        command=str(record.get("command", "")),
                        exit_code=int(record.get("exit", 1)),
                        source="execution_artifact",
                    )
                )
        for msg in execution.get("runner_failures", []):
            failures.append(FailureObservation(signature=str(msg)[:120], detail=str(msg)[:300], source="runner"))

    has_pass = any(v.exit_code == 0 for v in validations)
    has_fail = any(v.exit_code != 0 for v in validations) or bool(failures)
    confidence = 0.25
    if deliverables:
        confidence += 0.25
    if has_pass:
        confidence += 0.30
    if meaningful_changes:
        confidence += 0.10
    if has_fail:
        confidence -= 0.25

    summary = f"files={len(files)} source={len(source_files)} tests={len(tests)} docs={len(docs)}"

    return WorkspaceBeliefState(
        objective=objective,
        workspace_summary=summary,
        artifact_inventory=files,
        likely_deliverables=deliverables,
        runnable_entrypoints=entrypoints,
        test_inventory=tests,
        validation_observations=validations,
        known_failures=failures,
        scratch_artifacts=scratch,
        recent_meaningful_changes=meaningful_changes,
        completion_confidence=max(0.0, min(1.0, confidence)),
        materially_satisfied=bool(deliverables) and has_pass and not has_fail,
        unresolved_critical_issues=[f.signature for f in failures if f.is_critical],
    )


def update_beliefs(existing: WorkspaceBeliefState, observed: WorkspaceBeliefState) -> WorkspaceBeliefState:
    observed.action_history = existing.action_history
    observed.last_action_result = existing.last_action_result
    observed.repeated_patterns = existing.repeated_patterns
    return observed
