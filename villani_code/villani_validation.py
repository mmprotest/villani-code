from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from villani_code.villani_state import WorkspaceBeliefState

_ARTIFACT_KEYWORDS = {"build", "create", "make", "script", "dashboard", "app", "tool", "generate", "html", "report", "plotly", "visualization"}
_HTML_KEYWORDS = {"html", "dashboard", "plotly", "report", "visualization"}
_TEST_KEYWORDS = {"test", "pytest", "failing test", "fix tests"}
_COMMON_PY_ENTRY_NAMES = {"app.py", "main.py", "dashboard.py", "report.py", "script.py", "tool.py"}


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    validator_kind: str
    commands_run: list[str] = field(default_factory=list)
    artifacts_created: list[str] = field(default_factory=list)
    failure_signature: str | None = None
    stdout_excerpt: str | None = None
    stderr_excerpt: str | None = None
    notes: list[str] = field(default_factory=list)


def is_artifact_producing_task(objective: str) -> bool:
    lowered = objective.lower()
    return any(keyword in lowered for keyword in _ARTIFACT_KEYWORDS)


def _looks_like_html_task(objective: str) -> bool:
    lowered = objective.lower()
    return any(keyword in lowered for keyword in _HTML_KEYWORDS)


def _looks_like_test_fix_task(objective: str) -> bool:
    lowered = objective.lower()
    return any(keyword in lowered for keyword in _TEST_KEYWORDS)


def infer_primary_artifacts(objective: str, workspace_root: Path, touched_files: list[Path]) -> dict[str, Any]:
    resolved_touched = [p if p.is_absolute() else (workspace_root / p) for p in touched_files]
    py_candidates = [p for p in resolved_touched if p.suffix == ".py"]
    html_candidates = [p for p in resolved_touched if p.suffix == ".html"]
    if not py_candidates:
        discovered = [workspace_root / rel for rel in ("app.py", "main.py", "dashboard.py", "report.py") if (workspace_root / rel).exists()]
        py_candidates = discovered
    if py_candidates:
        py_candidates = sorted(
            py_candidates,
            key=lambda p: (0 if p.name.lower() in _COMMON_PY_ENTRY_NAMES else 1, len(p.as_posix())),
        )

    expected_outputs: list[Path] = []
    for match in re.findall(r"[\w./-]+\.(?:html|csv|json|txt)", objective, flags=re.IGNORECASE):
        expected_outputs.append((workspace_root / match).resolve())
    if _looks_like_html_task(objective) and not expected_outputs:
        base = py_candidates[0].stem if py_candidates else "report"
        expected_outputs.append((workspace_root / f"{base}.html").resolve())

    return {
        "primary_py": py_candidates[:2],
        "html_candidates": html_candidates,
        "expected_outputs": expected_outputs,
    }


def _run_command(cmd: list[str], cwd: Path, timeout_s: float = 8.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        return proc.returncode, proc.stdout[-1200:], proc.stderr[-1200:]
    except subprocess.TimeoutExpired as exc:
        out = str(exc.stdout or "")[-600:]
        err = str(exc.stderr or "")[-600:]
        return 124, out, (err + "\nTIMEOUT").strip()


def validate_villani_deliverable(
    objective: str,
    workspace_root: Path,
    touched_files: list[Path],
    belief_state: WorkspaceBeliefState,
) -> ValidationResult:
    inferred = infer_primary_artifacts(objective, workspace_root, touched_files)
    commands_run: list[str] = []

    if _looks_like_test_fix_task(objective):
        known = next((v.command for v in reversed(belief_state.validation_observations) if v.command), "")
        cmd = known if known else "pytest -q"
        code, out, err = _run_command(["bash", "-lc", cmd], workspace_root, timeout_s=15.0)
        commands_run.append(cmd)
        return ValidationResult(
            passed=code == 0,
            validator_kind="test_fix",
            commands_run=commands_run,
            failure_signature=None if code == 0 else f"test_command_failed:{cmd}",
            stdout_excerpt=out or None,
            stderr_excerpt=err or None,
            notes=[] if code == 0 else ["Targeted test command failed."],
        )

    primary_py: list[Path] = inferred["primary_py"]
    if primary_py and is_artifact_producing_task(objective):
        compile_targets = [p for p in primary_py if p.exists()]
        if compile_targets:
            compile_cmd = ["python", "-m", "py_compile", *[p.as_posix() for p in compile_targets]]
            code, out, err = _run_command(compile_cmd, workspace_root, timeout_s=8.0)
            commands_run.append(" ".join(compile_cmd))
            if code != 0:
                return ValidationResult(
                    passed=False,
                    validator_kind="python_artifact",
                    commands_run=commands_run,
                    failure_signature="python_compile_failed",
                    stdout_excerpt=out or None,
                    stderr_excerpt=err or None,
                    notes=["Compile check failed."],
                )

            entry = compile_targets[0]
            run_cmd = ["python", entry.as_posix()]
            code, out, err = _run_command(run_cmd, workspace_root, timeout_s=12.0)
            commands_run.append(" ".join(run_cmd))
            if code != 0:
                return ValidationResult(
                    passed=False,
                    validator_kind="python_artifact",
                    commands_run=commands_run,
                    failure_signature=f"python_runtime_failed:{entry.name}",
                    stdout_excerpt=out or None,
                    stderr_excerpt=err or None,
                    notes=["Script compiled but failed at runtime."],
                )

            expected_outputs: list[Path] = inferred["expected_outputs"]
            artifacts_created: list[str] = []
            for outp in expected_outputs:
                if outp.exists() and outp.is_file() and outp.stat().st_size > 0:
                    artifacts_created.append(outp.relative_to(workspace_root).as_posix())
            if expected_outputs and not artifacts_created:
                return ValidationResult(
                    passed=False,
                    validator_kind="python_artifact",
                    commands_run=commands_run,
                    failure_signature="missing_output_artifact",
                    stdout_excerpt=out or None,
                    stderr_excerpt=err or None,
                    notes=["Expected output artifacts were not created."],
                )

            if _looks_like_html_task(objective):
                html_targets = [workspace_root / rel for rel in artifacts_created if rel.endswith(".html")]
                for html in html_targets:
                    text = html.read_text(encoding="utf-8", errors="ignore")[:2000]
                    if "<html" not in text.lower() and "plotly" not in text.lower():
                        return ValidationResult(
                            passed=False,
                            validator_kind="html_artifact",
                            commands_run=commands_run,
                            artifacts_created=artifacts_created,
                            failure_signature="html_sanity_failed",
                            stdout_excerpt=out or None,
                            stderr_excerpt=err or None,
                            notes=["HTML output missing expected markers."],
                        )

            return ValidationResult(
                passed=True,
                validator_kind="python_artifact",
                commands_run=commands_run,
                artifacts_created=artifacts_created,
                stdout_excerpt=out or None,
                stderr_excerpt=err or None,
                notes=["Deliverable validated."],
            )

    return ValidationResult(
        passed=False,
        validator_kind="none",
        commands_run=[],
        failure_signature="no_validation_strategy",
        notes=["No validator strategy matched touched files/objective."],
    )


def apply_validation_result(beliefs: WorkspaceBeliefState, result: ValidationResult) -> None:
    beliefs.last_validation_passed = result.passed
    beliefs.last_validation_failed = not result.passed
    beliefs.last_validation_commands = list(result.commands_run)
    beliefs.last_failure_signature = result.failure_signature or ""
    beliefs.last_artifacts_created = list(result.artifacts_created)
    beliefs.last_validation_attempted = True
    if result.passed:
        beliefs.unresolved_critical_issues = []
    elif result.failure_signature:
        beliefs.unresolved_critical_issues = [result.failure_signature]
    if result.failure_signature:
        beliefs.last_repair_brief = {
            "failure_signature": result.failure_signature,
            "commands_run": list(result.commands_run),
            "stderr_excerpt": result.stderr_excerpt or "",
            "stdout_excerpt": result.stdout_excerpt or "",
            "expected_artifacts": list(result.artifacts_created),
            "notes": list(result.notes),
        }


def format_validation_artifact(result: ValidationResult) -> str:
    return json.dumps(
        {
            "validator_kind": result.validator_kind,
            "passed": result.passed,
            "commands": result.commands_run,
            "artifacts_created": result.artifacts_created,
            "failure_signature": result.failure_signature,
        },
        ensure_ascii=False,
    )
