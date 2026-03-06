from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


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


class VerificationEngine:
    """Compact adversarial verifier for changed files and command outcomes."""

    def __init__(self, repo: Path):
        self.repo = repo

    def verify(self, goal: str, changed_files: list[str], command_results: list[dict[str, Any]] | None = None) -> VerificationResult:
        findings: list[VerificationFinding] = []
        command_results = command_results or []
        commands_run = [str(c.get("command", "")) for c in command_results if c.get("command")]
        examined: list[str] = []

        for rel in changed_files[:12]:
            path = self.repo / rel
            if not path.exists() or not path.is_file():
                findings.append(VerificationFinding(FindingCategory.INCOMPLETE_EDIT, "Changed file is missing after edit", rel, "high"))
                continue
            examined.append(rel)
            text = path.read_text(encoding="utf-8", errors="replace")
            if "TODO" in text or "FIXME" in text:
                findings.append(VerificationFinding(FindingCategory.TEST_GAP, "TODO/FIXME still present in touched file", rel, "low"))
            if "import " in text and "  " in text.splitlines()[0:1][0] if text.splitlines() else False:
                findings.append(VerificationFinding(FindingCategory.INCONSISTENT_NAMING, "Suspicious formatting around imports", rel, "low"))

        for cmd in command_results:
            if int(cmd.get("exit", 0)) != 0:
                findings.append(VerificationFinding(FindingCategory.REGRESSION, f"Validation command failed: {cmd.get('command', '<unknown>')}", None, "high"))

        git_stat = subprocess.run(["git", "diff", "--stat"], cwd=self.repo, capture_output=True, text=True)
        stat_text = git_stat.stdout.strip()
        if stat_text:
            last = stat_text.splitlines()[-1]
            if " files changed" in last:
                try:
                    files_changed = int(last.split(" files changed", 1)[0].split()[-1])
                    if files_changed > 8:
                        findings.append(VerificationFinding(FindingCategory.SUSPICIOUS_BREADTH, f"Large edit batch detected ({files_changed} files)", None, "medium"))
                except (ValueError, IndexError):
                    pass

        confidence = max(0.05, 0.95 - (0.12 * len(findings)))
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
        )


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

        if "denied" in text or "permission" in text or "sandbox" in text:
            category = FailureCategory.PERMISSION_OR_SANDBOX_ISSUE
            retryable = False
            strategy = "Avoid blocked operations and choose repo-local alternatives."
        elif "pytest" in text or "test" in text:
            category = FailureCategory.TEST_FAILURE
            strategy = "Isolate failing tests and separate pre-existing failures from introduced regressions."
        elif "no progress" in text or "blocked" in text:
            category = FailureCategory.REPEATED_NO_PROGRESS
            strategy = "Switch strategy: decompose objective and execute one bounded step."
        elif "verify" in text or "verification" in text:
            category = FailureCategory.VERIFICATION_FAILURE
            strategy = "Rollback or repair the failing change and re-run adversarial verification."
        elif "dependency" in text or "lock" in text:
            category = FailureCategory.DEPENDENCY_BREAKAGE
            strategy = "Inspect lockfiles/manifests before unrelated edits."
        elif "not found" in text or "unknown" in text:
            category = FailureCategory.MISSING_CONTEXT
            strategy = "Gather targeted evidence (read files, grep symbols) before editing."

        count = self._counts.get(category, 0) + 1
        self._counts[category] = count
        if count >= 3 and category != FailureCategory.REPEATED_NO_PROGRESS:
            category = FailureCategory.REPEATED_NO_PROGRESS
            strategy = "Repeated failure pattern detected; change approach and reduce blast radius."

        return FailureEvent(category=category, cause_summary=reason, evidence=evidence[:280], retryable=retryable, suggested_strategy=strategy, occurrence_count=count)


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


@dataclass(slots=True)
class TakeoverConfig:
    max_files_per_wave: int = 4
    max_commands_per_wave: int = 3
    max_waves: int = 3
    min_confidence: float = 0.55


@dataclass(slots=True)
class TakeoverState:
    repo_summary: str
    discovered_opportunities: list[Opportunity] = field(default_factory=list)
    completed_waves: list[dict[str, Any]] = field(default_factory=list)
    skipped_opportunities: list[Opportunity] = field(default_factory=list)
    blocked_opportunities: list[Opportunity] = field(default_factory=list)
    current_risk_level: str = "low"


class TakeoverPlanner:
    def __init__(self, repo: Path):
        self.repo = repo

    def build_repo_summary(self) -> str:
        files = [p for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.as_posix()]
        py = sum(1 for p in files if p.suffix == ".py")
        tests = sum(1 for p in files if "tests" in p.parts)
        md = sum(1 for p in files if p.suffix == ".md")
        return f"files={len(files)} py={py} tests={tests} docs={md}"

    def discover_opportunities(self) -> list[Opportunity]:
        ops: list[Opportunity] = []
        if (self.repo / "tests").exists():
            ops.append(Opportunity("Validate baseline tests", "broken_tests", 0.92, 0.86, ["tests/"], "tests directory present", "small", "run pytest -q"))
        todos = subprocess.run(["rg", "-n", "TODO|FIXME", "."], cwd=self.repo, capture_output=True, text=True)
        if todos.stdout.strip():
            ops.append(Opportunity("Triage TODO/FIXME cluster", "todo_fixme_cluster", 0.68, 0.72, [], todos.stdout.splitlines()[0], "medium", "resolve highest-signal TODO"))
        if (self.repo / "README.md").exists():
            ops.append(Opportunity("Audit docs drift", "stale_docs", 0.55, 0.64, ["README.md"], "README present", "small", "sync docs with current CLI"))
        return sorted(ops, key=lambda o: (o.priority * 0.7 + o.confidence * 0.3), reverse=True)
