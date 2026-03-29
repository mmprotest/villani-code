from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from villani_code.repo_rules import (
    classify_repo_path,
    is_authoritative_doc_path,
    is_ignored_repo_path,
)


class FindingCategory(str, Enum):
    REGRESSION = "regression"
    INCOMPLETE_EDIT = "incomplete_edit"
    BROKEN_REFERENCE = "broken_reference"
    STALE_DOC = "stale_doc_or_comment"
    INCONSISTENT_NAMING = "inconsistent_naming"
    HIDDEN_SIDE_EFFECT = "hidden_side_effect"
    FAILED_ASSUMPTION = "failed_assumption"
    TEST_GAP = "test_gap"
    SUSPICIOUS_BREADTH = "suspicious_broad_edit"


class VerificationStatus(str, Enum):
    PASS = "pass"
    REPAIRED = "repaired"
    FAIL = "fail"
    UNCERTAIN = "uncertain"


class TaskContract(str, Enum):
    EFFECTFUL = "effectful"
    VALIDATION = "validation"
    INSPECTION = "inspection"
    LOCALIZE = "localize"
    INSPECT = "inspect"
    REPRODUCE = "reproduce"
    NARROW_FIX = "narrow_fix"
    BROAD_FIX = "broad_fix"
    VALIDATE = "validate"
    IMPLEMENT = "implement"
    CONTAIN_CHANGE = "contain_change"
    CLEANUP = "cleanup"
    SUMMARIZE = "summarize"


def normalize_task_contract(value: str | TaskContract | None) -> TaskContract:
    if isinstance(value, TaskContract):
        return value
    raw = str(value or "").strip().lower()
    aliases = {
        "inspection": TaskContract.INSPECT,
        "inspect": TaskContract.INSPECT,
        "validation": TaskContract.VALIDATE,
        "validate": TaskContract.VALIDATE,
        "effectful": TaskContract.NARROW_FIX,
        "inspect_workspace": TaskContract.INSPECT,
        "choose_project_direction": TaskContract.INSPECT,
        "scaffold_project": TaskContract.IMPLEMENT,
        "implement_vertical_slice": TaskContract.IMPLEMENT,
        "validate_project": TaskContract.VALIDATE,
        "summarize_outcome": TaskContract.SUMMARIZE,
    }
    if raw in aliases:
        return aliases[raw]
    for c in TaskContract:
        if c.value == raw:
            return c
    return TaskContract.INSPECT


def is_effectful_contract(value: str | TaskContract | None) -> bool:
    contract = normalize_task_contract(value)
    return contract in {
        TaskContract.EFFECTFUL,
        TaskContract.NARROW_FIX,
        TaskContract.BROAD_FIX,
        TaskContract.IMPLEMENT,
        TaskContract.CLEANUP,
        TaskContract.CONTAIN_CHANGE,
    }


def contract_allows_edits(value: str | TaskContract | None) -> bool:
    return is_effectful_contract(value)


def contract_requires_validation(value: str | TaskContract | None) -> bool:
    contract = normalize_task_contract(value)
    return contract in {
        TaskContract.VALIDATION,
        TaskContract.VALIDATE,
        TaskContract.REPRODUCE,
        TaskContract.NARROW_FIX,
        TaskContract.BROAD_FIX,
        TaskContract.IMPLEMENT,
        TaskContract.CONTAIN_CHANGE,
        TaskContract.CLEANUP,
    }


def contract_discourages_editing(value: str | TaskContract | None) -> bool:
    contract = normalize_task_contract(value)
    return contract in {
        TaskContract.INSPECTION,
        TaskContract.INSPECT,
        TaskContract.LOCALIZE,
        TaskContract.REPRODUCE,
        TaskContract.VALIDATION,
        TaskContract.VALIDATE,
        TaskContract.SUMMARIZE,
    }


@dataclass(slots=True)
class VerificationFinding:
    category: FindingCategory
    message: str
    file_path: str | None = None
    severity: str = "medium"


@dataclass(slots=True)
class VerificationResult:
    status: VerificationStatus
    confidence_score: float
    findings: list[VerificationFinding]
    commands_run: list[str]
    files_examined: list[str]
    repair_attempted: bool
    summary: str
    repeated_verification_state: bool = False


class VerificationEngine:
    """Compact adversarial verifier for changed files and command outcomes."""

    def __init__(self, repo: Path, logger: Any | None = None):
        self.repo = repo
        self._logger = logger or (lambda _msg: None)
        self._last_finding_fingerprints: tuple[str, ...] = ()

    def verify(
        self,
        goal: str,
        changed_files: list[str],
        command_results: list[dict[str, Any]] | None = None,
        validation_artifacts: list[str] | None = None,
        intended_targets: list[str] | None = None,
        target_existence_expectation: dict[str, bool] | None = None,
        before_contents: dict[str, str] | None = None,
    ) -> VerificationResult:
        findings: list[VerificationFinding] = []
        command_results = command_results or []
        validation_artifacts = validation_artifacts or []
        commands_run = [
            str(c.get("command", "")) for c in command_results if c.get("command")
        ]
        examined: list[str] = []
        intended_targets = [
            self._normalize_repo_path(p) for p in (intended_targets or changed_files)
        ]
        target_existence_expectation = {
            self._normalize_repo_path(k): v
            for k, v in (target_existence_expectation or {}).items()
        }
        before_contents = {
            self._normalize_repo_path(k): v for k, v in (before_contents or {}).items()
        }
        git_diff_targets = set(self._git_diff_name_only())

        for rel in intended_targets[:12]:
            if is_ignored_repo_path(rel):
                continue
            expected_to_exist = target_existence_expectation.get(rel, True)
            path = self.repo / rel
            if expected_to_exist and (not path.exists() or not path.is_file()):
                findings.append(
                    VerificationFinding(
                        FindingCategory.INCOMPLETE_EDIT,
                        "Changed file is missing after edit",
                        rel,
                        "high",
                    )
                )
                continue
            examined.append(rel)
            if expected_to_exist and rel in before_contents:
                current = path.read_text(encoding="utf-8", errors="replace")
                if current == before_contents[rel] and rel not in git_diff_targets:
                    findings.append(
                        VerificationFinding(
                            FindingCategory.FAILED_ASSUMPTION,
                            "No effective change detected for intended target",
                            rel,
                            "medium",
                        )
                    )
                continue

        for rel in changed_files[:12]:
            if is_ignored_repo_path(rel):
                continue
            path = self.repo / rel
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "TODO" in text or "FIXME" in text:
                findings.append(
                    VerificationFinding(
                        FindingCategory.TEST_GAP,
                        "TODO/FIXME still present in touched file",
                        rel,
                        "low",
                    )
                )
            if (
                "import " in text and "  " in text.splitlines()[0:1][0]
                if text.splitlines()
                else False
            ):
                findings.append(
                    VerificationFinding(
                        FindingCategory.INCONSISTENT_NAMING,
                        "Suspicious formatting around imports",
                        rel,
                        "low",
                    )
                )

        for cmd in command_results:
            if int(cmd.get("exit", 0)) != 0:
                findings.append(
                    VerificationFinding(
                        FindingCategory.REGRESSION,
                        f"Validation command failed: {cmd.get('command', '<unknown>')}",
                        None,
                        "high",
                    )
                )

        git_stat = subprocess.run(
            ["git", "diff", "--stat"], cwd=self.repo, capture_output=True, text=True
        )
        stat_text = git_stat.stdout.strip()
        if stat_text:
            last = stat_text.splitlines()[-1]
            if " files changed" in last:
                try:
                    files_changed = int(last.split(" files changed", 1)[0].split()[-1])
                    if files_changed > 8:
                        findings.append(
                            VerificationFinding(
                                FindingCategory.SUSPICIOUS_BREADTH,
                                f"Large edit batch detected ({files_changed} files)",
                                None,
                                "medium",
                            )
                        )
                except (ValueError, IndexError):
                    pass

        if not intended_targets and not changed_files and not validation_artifacts:
            findings.append(
                VerificationFinding(
                    FindingCategory.FAILED_ASSUMPTION,
                    "No intervention or validation evidence produced.",
                    None,
                    "medium",
                )
            )

        findings = self._reconcile_findings(
            findings, intended_targets, before_contents, git_diff_targets
        )
        finding_fingerprints = tuple(
            sorted(
                f"{f.category.value}|{self._normalize_repo_path(f.file_path)}|{f.message.strip().lower()}"
                for f in findings
            )
        )
        repeated_verification_state = finding_fingerprints == self._last_finding_fingerprints
        self._last_finding_fingerprints = finding_fingerprints

        severity_penalty = {"high": 0.18, "medium": 0.1, "low": 0.05}
        confidence = 0.9
        for finding in findings:
            confidence -= severity_penalty.get(finding.severity, 0.1)
        command_failures = sum(1 for cmd in command_results if int(cmd.get("exit", 0)) != 0)
        if command_failures:
            confidence -= 0.12 * command_failures
        if any(f.category == FindingCategory.SUSPICIOUS_BREADTH for f in findings):
            confidence -= 0.06
        if not intended_targets and not changed_files and not validation_artifacts:
            confidence -= 0.08
        if repeated_verification_state:
            confidence -= 0.03
        confidence = max(0.05, min(0.95, confidence))

        if not findings:
            status = VerificationStatus.PASS
            summary = f"Adversarial review passed for {len(examined)} file(s)."
        elif any(f.severity == "high" for f in findings):
            status = VerificationStatus.FAIL
            summary = "Adversarial review found high-risk issues."
        else:
            status = VerificationStatus.UNCERTAIN
            summary = "Adversarial review found non-blocking risks."

        return VerificationResult(
            status=status,
            confidence_score=round(confidence, 2),
            findings=findings,
            commands_run=commands_run,
            files_examined=examined,
            repair_attempted=False,
            summary=summary,
            repeated_verification_state=repeated_verification_state,
        )

    def _git_diff_name_only(self) -> list[str]:
        proc = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        return [
            self._normalize_repo_path(line.strip())
            for line in proc.stdout.splitlines()
            if line.strip()
        ]

    def _reconcile_findings(
        self,
        findings: list[VerificationFinding],
        intended_targets: list[str],
        before_contents: dict[str, str],
        git_diff_targets: set[str],
    ) -> list[VerificationFinding]:
        reconciled: list[VerificationFinding] = []
        intended_set = set(intended_targets)
        for finding in findings:
            rel = (
                self._normalize_repo_path(finding.file_path)
                if finding.file_path
                else None
            )
            if (
                finding.category == FindingCategory.INCOMPLETE_EDIT
                and rel
                and rel in intended_set
            ):
                path = self.repo / rel
                if path.exists():
                    self._logger(
                        f"Verification finding removed due to direct evidence: {finding.category.value}: {finding.message}"
                    )
                    continue
            if (
                finding.category == FindingCategory.FAILED_ASSUMPTION
                and rel
                and rel in intended_set
            ):
                path = self.repo / rel
                before = before_contents.get(rel)
                if path.exists() and before is not None:
                    current = path.read_text(encoding="utf-8", errors="replace")
                    if current != before or rel in git_diff_targets:
                        self._logger(
                            f"Verification finding removed due to direct evidence: {finding.category.value}: {finding.message}"
                        )
                        continue
            reconciled.append(finding)
        return reconciled

    @staticmethod
    def _normalize_repo_path(path: str | None) -> str:
        if not path:
            return ""
        return path.replace("\\", "/").lstrip("./")


class FailureCategory(str, Enum):
    MODEL_CONFUSION = "model_confusion"
    REPO_AMBIGUITY = "repo_ambiguity"
    MISSING_CONTEXT = "missing_context"
    TOOL_FAILURE = "tool_failure"
    SHELL_COMMAND_FAILURE = "shell_command_failure"
    TEST_FAILURE = "test_failure"
    ENV_INSTABILITY = "env_instability"
    DEPENDENCY_BREAKAGE = "dependency_breakage"
    VERIFICATION_FAILURE = "verification_failure"
    PERMISSION_OR_SANDBOX_ISSUE = "permission_or_sandbox_issue"
    SPEC_UNCERTAINTY = "spec_uncertainty"
    EXCESSIVE_BLAST_RADIUS = "excessive_blast_radius"
    REPEATED_NO_PROGRESS = "repeated_no_progress"


@dataclass(slots=True)
class FailureEvent:
    category: FailureCategory
    cause_summary: str
    evidence: str
    retryable: bool
    suggested_strategy: str
    occurrence_count: int = 1


class FailureClassifier:
    def __init__(self) -> None:
        self._counts: dict[FailureCategory, int] = {}

    def classify(self, reason: str, evidence: str) -> FailureEvent:
        text = f"{reason}\n{evidence}".lower()
        category = FailureCategory.TOOL_FAILURE
        retryable = True
        strategy = "Retry once with narrower scope and explicit command output capture."

        if "read-before-edit policy" in text:
            category = FailureCategory.TOOL_FAILURE
            strategy = "Use Write to create missing files and read existing files before editing."
        elif "denied" in text or "permission" in text or "sandbox" in text:
            category = FailureCategory.PERMISSION_OR_SANDBOX_ISSUE
            retryable = False
            strategy = "Avoid blocked operations and choose repo-local alternatives."
        elif "pytest" in text or "test" in text:
            category = FailureCategory.TEST_FAILURE
            strategy = "Isolate failing tests and separate pre-existing failures from introduced regressions."
        elif "no progress" in text or "blocked" in text:
            category = FailureCategory.REPEATED_NO_PROGRESS
            strategy = (
                "Switch strategy: decompose objective and execute the next highest-leverage step."
            )
        elif "verify" in text or "verification" in text:
            category = FailureCategory.VERIFICATION_FAILURE
            strategy = "Rollback or repair the failing change and re-run adversarial verification."
        elif "dependency" in text or "lock" in text:
            category = FailureCategory.DEPENDENCY_BREAKAGE
            strategy = "Inspect lockfiles/manifests before unrelated edits."
        elif "not found" in text or "unknown" in text:
            category = FailureCategory.MISSING_CONTEXT
            strategy = (
                "Gather targeted evidence (read files, grep symbols) before editing."
            )

        count = self._counts.get(category, 0) + 1
        self._counts[category] = count
        if count >= 3 and category != FailureCategory.REPEATED_NO_PROGRESS:
            category = FailureCategory.REPEATED_NO_PROGRESS
            strategy = "Repeated failure pattern detected; change approach and prioritize a different intervention."

        return FailureEvent(
            category=category,
            cause_summary=reason,
            evidence=evidence[:280],
            retryable=retryable,
            suggested_strategy=strategy,
            occurrence_count=count,
        )


@dataclass(slots=True)
class Opportunity:
    title: str
    category: str
    priority: float
    confidence: float
    affected_files: list[str]
    evidence: str
    blast_radius: str
    proposed_next_action: str
    task_contract: str


@dataclass(slots=True)
class TakeoverConfig:
    max_files_per_wave: int = 0
    max_commands_per_wave: int = 2
    max_waves: int = 3
    max_total_task_attempts: int = 6
    min_confidence: float = 0.60
    stagnation_cycle_limit: int = 2


@dataclass(slots=True)
class TakeoverState:
    repo_summary: str
    discovered_opportunities: list[Opportunity] = field(default_factory=list)
    completed_waves: list[dict[str, Any]] = field(default_factory=list)
    skipped_opportunities: list[Opportunity] = field(default_factory=list)
    blocked_opportunities: list[Opportunity] = field(default_factory=list)
    current_risk_level: str = "low"


class TakeoverPlanner:
    _RANK_ORDER = {
        "Bootstrap minimal tests": 0,
        "Validate baseline importability": 1,
        "Audit tracked runtime artifacts": 2,
        "Triage TODO/FIXME cluster": 3,
        "Audit missing usage docs": 4,
        "Audit docs drift": 5,
        "Inspect repo for highest-leverage improvement": 6,
    }

    def __init__(self, repo: Path, *, enable_fallback: bool = True):
        self.repo = repo
        self.enable_fallback = enable_fallback

    def _find_todo_fixme_matches(self) -> list[str]:
        rg_path = shutil.which("rg")
        if rg_path:
            try:
                result = subprocess.run(
                    [
                        "rg",
                        "-n",
                        "TODO|FIXME",
                        ".",
                        "--glob",
                        "!.git/**",
                        "--glob",
                        "!.venv/**",
                        "--glob",
                        "!venv/**",
                        "--glob",
                        "!**/__pycache__/**",
                        "--glob",
                        "!**/.pytest_cache/**",
                        "--glob",
                        "!**/.mypy_cache/**",
                        "--glob",
                        "!**/.ruff_cache/**",
                        "--glob",
                        "!**/.ipynb_checkpoints/**",
                        "--glob",
                        "!**/.vscode/**",
                        "--glob",
                        "!**/.idea/**",
                        "--glob",
                        "!**/.villani_code/**",
                        "--glob",
                        "!build/**",
                        "--glob",
                        "!dist/**",
                        "--glob",
                        "!node_modules/**",
                    ],
                    cwd=self.repo,
                    capture_output=True,
                    text=True,
                )
                if result.returncode in {0, 1}:
                    return [line for line in result.stdout.splitlines() if line.strip()]
            except (FileNotFoundError, OSError, subprocess.SubprocessError):
                pass

        matches: list[str] = []
        for path in self.repo.rglob("*"):
            if len(matches) >= 50:
                break
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo).as_posix()
            if is_ignored_repo_path(rel):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                with path.open("r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, start=1):
                        if "TODO" in line or "FIXME" in line:
                            matches.append(f"{rel}:{line_no}: {line.strip()}")
                            if len(matches) >= 50:
                                break
            except OSError:
                continue

        return matches

    def build_repo_summary(self) -> str:
        files = [
            p
            for p in self.repo.rglob("*")
            if p.is_file()
            and not is_ignored_repo_path(p.relative_to(self.repo).as_posix())
        ]
        py = sum(1 for p in files if p.suffix == ".py")
        rel_files = [p.relative_to(self.repo).as_posix() for p in files]
        tests = sum(1 for rel in rel_files if self._is_meaningful_test_file(rel))
        md = sum(1 for p in files if p.suffix == ".md")
        has_tests = self._has_meaningful_tests(rel_files)
        return f"files={len(files)} py={py} tests={tests} docs={md} has_tests={int(has_tests)}"

    def discover_opportunities(self) -> list[Opportunity]:
        ops: list[Opportunity] = []
        authoritative_files = self._authoritative_files()
        python_files = self._python_source_files(authoritative_files)
        has_tests = self._has_meaningful_tests(authoritative_files)
        docs = self._discover_authoritative_docs()

        if python_files and not has_tests:
            ops.append(
                Opportunity(
                    "Bootstrap minimal tests",
                    "testing",
                    0.95,
                    0.78,
                    python_files[:3],
                    "Python repo has source code but no tests directory or test files",
                    "small",
                    "audit package layout and add a minimal baseline test scaffold",
                    TaskContract.EFFECTFUL.value,
                )
            )
        if python_files:
            ops.append(
                Opportunity(
                    "Validate baseline importability",
                    "validation",
                    0.9,
                    0.72,
                    python_files[:3],
                    "Python repo detected; baseline validation is a reasonable first-pass autonomous task",
                    "small",
                    "inspect package entry points and validate importable baseline",
                    TaskContract.VALIDATION.value,
                )
            )
        tracked_artifacts = self._tracked_runtime_artifacts()
        if tracked_artifacts:
            ops.append(
                Opportunity(
                    "Audit tracked runtime artifacts",
                    "hygiene",
                    0.84,
                    0.74,
                    tracked_artifacts[:5],
                    "repository contains likely junk artifacts that may need cleanup",
                    "small",
                    "inspect tracked caches/checkpoints/editor artifacts and propose cleanup",
                    TaskContract.INSPECTION.value,
                )
            )
        todo_matches = self._find_todo_fixme_matches()
        if todo_matches:
            ops.append(
                Opportunity(
                    "Triage TODO/FIXME cluster",
                    "todo_fixme_cluster",
                    0.68,
                    0.72,
                    [],
                    todo_matches[0],
                    "medium",
                    "resolve highest-signal TODO",
                    TaskContract.EFFECTFUL.value,
                )
            )
        if python_files and self._has_minimal_docs(docs):
            ops.append(
                Opportunity(
                    "Audit missing usage docs",
                    "docs",
                    0.62,
                    0.68,
                    docs[:1],
                    "code exists but documentation coverage appears sparse",
                    "small",
                    "inspect README and package layout for obvious docs coverage gaps",
                    TaskContract.EFFECTFUL.value,
                )
            )
        if docs and self._has_authoritative_docs_drift(docs):
            ops.append(
                Opportunity(
                    "Audit docs drift",
                    "stale_docs",
                    0.55,
                    0.64,
                    docs,
                    "Authoritative docs may not match current module layout",
                    "small",
                    "sync docs with current CLI",
                    TaskContract.EFFECTFUL.value,
                )
            )
        if not ops and self.enable_fallback:
            ops.append(
                Opportunity(
                    "Inspect repo for highest-leverage improvement",
                    "fallback",
                    0.5,
                    0.55,
                    [],
                    "no stronger heuristic fired, but repo still deserves inspection",
                    "small",
                    "read README, inspect key package/config files, and identify the highest-leverage improvement",
                    TaskContract.INSPECTION.value,
                )
            )
        ops = [op for op in ops if self._is_authoritative_opportunity(op)]
        return sorted(ops, key=self._rank_key)

    def _rank_key(self, op: Opportunity) -> tuple[int, float]:
        rank = self._RANK_ORDER.get(op.title, 99)
        score = op.priority * 0.7 + op.confidence * 0.3
        return (rank, -score)

    def _authoritative_files(self) -> list[str]:
        files: list[str] = []
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo).as_posix()
            if is_ignored_repo_path(rel):
                continue
            files.append(rel)
        return sorted(files)

    def _python_source_files(self, files: list[str]) -> list[str]:
        return [
            rel
            for rel in files
            if rel.endswith(".py")
            and not rel.startswith("tests/")
            and not Path(rel).name.startswith("test_")
        ]

    def _is_meaningful_test_file(self, rel: str) -> bool:
        path = Path(rel)
        name = path.name
        if not name.endswith(".py"):
            return False
        return rel.startswith("tests/") or name.startswith("test_")

    def _has_meaningful_tests(self, files: list[str]) -> bool:
        return any(self._is_meaningful_test_file(rel) for rel in files)

    def _tracked_runtime_artifacts(self) -> list[str]:
        try:
            proc = subprocess.run(
                ["git", "ls-files"], cwd=self.repo, capture_output=True, text=True
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return []
        if proc.returncode != 0:
            return []
        tracked: list[str] = []
        for line in proc.stdout.splitlines():
            rel = line.strip()
            if not rel:
                continue
            cls = classify_repo_path(rel)
            if cls in {"runtime_artifact", "editor_artifact"}:
                tracked.append(rel)
        return sorted(set(tracked))

    def _has_minimal_docs(self, docs: list[str]) -> bool:
        if not docs:
            return True
        return len(docs) == 1 and docs[0] in {"README.md", "README.rst"}

    def _discover_authoritative_docs(self) -> list[str]:
        docs: list[str] = []
        for path in self.repo.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo).as_posix()
            if is_authoritative_doc_path(rel):
                docs.append(rel)
        return sorted(docs)

    def _has_authoritative_docs_drift(self, docs: list[str]) -> bool:
        if not docs:
            return False
        readme = self.repo / "README.md"
        src = self.repo / "villani_code"
        if (
            readme.exists()
            and src.exists()
            and any(p.name == "__init__.py" for p in src.rglob("__init__.py"))
        ):
            text = readme.read_text(encoding="utf-8", errors="replace").lower()
            return "villani" not in text
        return False

    def _is_authoritative_opportunity(self, op: Opportunity) -> bool:
        if op.category == "stale_docs":
            return all(is_authoritative_doc_path(p) for p in op.affected_files)
        return not any(
            is_ignored_repo_path(p) or classify_repo_path(p) != "authoritative"
            for p in op.affected_files
        )
