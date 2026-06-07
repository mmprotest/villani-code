from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

MAX_FINAL_CONSISTENCY_CHECK_CHARS = 3000
MAX_FINAL_CHECK_MODEL_VISIBLE_CHARS = 3000
HIGH_RISK_WARNING = (
    "Finalization is high risk because at least one explicit requirement is weakly verified, "
    "contradicted, or unchecked. Fix the gap, run stronger validation, or explicitly justify "
    "why no further action is possible."
)
WEAKER_STATE_WARNING = (
    "Current evidence is weaker than an earlier state. Fix the regression, revert to the "
    "stronger state, or justify finalizing despite weaker evidence."
)

FINAL_CONSISTENCY_REQUEST = """Before finalizing, answer these exact questions concisely:
1. What did the task explicitly require?
2. What files, code, configuration, services, or state did I change or create?
3. What evidence proves each explicit requirement is satisfied?
4. Which requirements are only weakly supported or not yet verified?
5. Did I re-read every final required deliverable after writing it?
6. Did I run the strongest available validation without masking failures?
7. Did any previous validation contradict the current conclusion?
8. Did I identify any concrete defect that has not been fixed and revalidated?
Keep the response concise."""

_FILTER_RE = re.compile(r"\|\s*(?:head|tail|grep|awk|sed)\b", re.IGNORECASE)
_MASK_RE = re.compile(r"(?:\|\|\s*(?:true|echo\b)|;\s*echo\b)", re.IGNORECASE)
_PRESERVED_PIPE_STATUS_RE = re.compile(r"\bPIPESTATUS\s*\[|\$\{?PIPESTATUS", re.IGNORECASE)
_EXISTENCE_ONLY_RE = re.compile(r"^\s*(?:test\s+-(?:e|f|d)|\[\s+-(?:e|f|d)|ls\b|stat\b)", re.IGNORECASE)
_EVIDENCE_CLAIM_RE = re.compile(r"\b(?:evidence|proves?|verified|validation|tests?|check(?:ed)?|re-read|contents?|output|exit code|command)\b", re.IGNORECASE)
_DISPLAY_SUBSTITUTION_RE = re.compile(r"(?:echo|printf)\s+[^\n]*\$\([^)]*\)", re.IGNORECASE)
_FAILURE_RE = re.compile(r"\b(?:fail(?:ed|ure|ures|ing)?|error(?:s)?|broken|wrong|mismatch|undefined|missing|unresolved|defect|bug)\b", re.IGNORECASE)
_DISMISS_RE = re.compile(r"\b(?:false alarm|not (?:a )?(?:bug|defect|issue)|resolved|fixed|no longer|revalidated)\b", re.IGNORECASE)
_WEAK_RE = re.compile(r"\b(?:weak(?:ly)?|unverified|not (?:yet )?verified|no evidence|cannot (?:point|prove)|unchecked|not re-read|did not re-read)\b", re.IGNORECASE)
_CONTRADICTION_RE = re.compile(r"\b(?:contradict|inconsistent|unstable|previously failed|earlier failed|flaky)\b", re.IGNORECASE)
_FILE_RE = re.compile(r"(?<![\w/.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+|(?<![\w.-])[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+")
_PROMPT_FILE_ACTION_RE = re.compile(r"\b(?:creat(?:e|ing)|modif(?:y|ying)|report(?:ing)?|sav(?:e|ing)|writ(?:e|ing)|output(?:ting)?|submit(?:ting)?)\b", re.IGNORECASE)
_SUCCESS_RE = re.compile(r"\b(?:passed|pass|success|successful|ok|completed)\b", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n[truncated; full details written to debug artifacts]"
    return text[: max(0, limit - len(suffix))] + suffix


def normalize_command(command: str) -> str:
    value = " ".join(command.strip().split())
    value = re.sub(r"\s+", " ", value)
    return value


def validation_mask_reasons(command: str) -> list[str]:
    reasons: list[str] = []
    if _FILTER_RE.search(command) and not _PRESERVED_PIPE_STATUS_RE.search(command):
        reasons.append("filtered or partial pipeline output")
    if _MASK_RE.search(command):
        reasons.append("exit status may be masked")
    if _DISPLAY_SUBSTITUTION_RE.search(command):
        reasons.append("command substitution used only for display")
    if _EXISTENCE_ONLY_RE.search(command):
        reasons.append("file existence or metadata only")
    return reasons


def is_masked_validation_command(command: str) -> bool:
    return bool(validation_mask_reasons(command))


def _result_signature(stdout: str, stderr: str, exit_code: int) -> dict[str, Any]:
    text = f"{stdout}\n{stderr}".lower()
    failures = len(re.findall(r"\b(?:failed|failure|failures|errors?|broken)\b", text))
    successes = len(_SUCCESS_RE.findall(text))
    numbers = [float(value) for value in _NUMERIC_RE.findall(text)[:20]]
    return {"exit_code": exit_code, "failure_keywords": failures, "success_keywords": successes, "numbers": numbers}


def _materially_different(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("exit_code") != right.get("exit_code"):
        return True
    if left.get("failure_keywords") != right.get("failure_keywords"):
        return True
    if bool(left.get("success_keywords")) != bool(right.get("success_keywords")):
        return True
    a = left.get("numbers") or []
    b = right.get("numbers") or []
    if len(a) != len(b):
        return bool(a or b)
    return any(abs(x - y) > max(1.0, abs(x) * 0.1) for x, y in zip(a, b))


def _is_scratch(path: str, temporary_paths: set[str]) -> bool:
    normalized = path.replace("\\", "/")
    lowered = normalized.lower()
    if normalized in temporary_paths:
        return True
    parts = set(lowered.split("/"))
    return lowered.startswith("/tmp/") or bool(parts & {".cache", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}) or lowered.endswith((".tmp", ".temp", ".swp", "~"))


@dataclass(slots=True)
class DeliverableRecord:
    path: str
    reasons: list[str] = field(default_factory=list)
    writes: int = 0
    last_write_revision: int = 0
    last_read_revision: int = -1
    reread_after_write: bool = False


@dataclass(slots=True)
class IssueRecord:
    summary: str
    identified_revision: int
    resolved: bool = False
    resolution: str = ""


@dataclass(slots=True)
class ValidationRecord:
    command: str
    normalized_command: str
    exit_code: int
    workspace_revision: int
    weakened: bool
    weakening_reasons: list[str]
    clean: bool
    strength: int
    result_signature: dict[str, Any]
    unstable: bool = False


class CompletionConsistencyTracker:
    def __init__(self, prompt: str, repo: Path):
        self.prompt = prompt
        self.repo = repo.resolve()
        self.workspace_revision = 0
        self.deliverables: dict[str, DeliverableRecord] = {}
        self.validations: list[ValidationRecord] = []
        self.issues: list[IssueRecord] = []
        self.checks: list[dict[str, Any]] = []
        self.high_risk_warning_count = 0
        self.last_known_better: dict[str, Any] | None = None
        self.current_risk: dict[str, Any] = {"level": "high", "reasons": ["final consistency check not run"]}
        self._temporary_paths: set[str] = set()
        self._prompt_mentions_files = bool(_PROMPT_FILE_ACTION_RE.search(prompt))
        for path in _FILE_RE.findall(prompt):
            self._mark_possible(path, "explicitly mentioned in task prompt")

    def _normalize_path(self, path: str) -> str:
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                return candidate.resolve(strict=False).relative_to(self.repo).as_posix()
            except ValueError:
                return candidate.as_posix()
        return candidate.as_posix().lstrip("./")

    def _mark_possible(self, path: str, reason: str) -> None:
        normalized = self._normalize_path(path)
        if not normalized or _is_scratch(normalized, self._temporary_paths):
            return
        item = self.deliverables.setdefault(normalized, DeliverableRecord(path=normalized))
        if reason not in item.reasons:
            item.reasons.append(reason)

    def observe_agent_text(self, text: str) -> None:
        lowered = text.lower()
        paths = self._paths_in_text(text)
        if "temporary" in lowered or "scratch" in lowered:
            self._temporary_paths.update(paths)
        if re.search(r"\b(?:required|deliverable|final (?:output|artifact))\b", text, re.IGNORECASE):
            for path in paths:
                self._mark_possible(path, "agent described file as required or final")
        if _FAILURE_RE.search(text) and not _DISMISS_RE.search(text):
            summary = " ".join(text.strip().split())[:500]
            if summary and not any(issue.summary == summary and not issue.resolved for issue in self.issues):
                self.issues.append(IssueRecord(summary=summary, identified_revision=self.workspace_revision))
        elif _DISMISS_RE.search(text):
            for issue in reversed(self.issues):
                if not issue.resolved:
                    issue.resolved = True
                    issue.resolution = "agent dismissed or reported resolution with evidence"
                    break

    def _paths_in_text(self, text: str) -> set[str]:
        return {self._normalize_path(value.rstrip(".,:;")) for value in _FILE_RE.findall(text)}

    def record_write(self, paths: Iterable[str], *, created: bool = False) -> None:
        self._update_best_state()
        normalized_paths = [self._normalize_path(path) for path in paths if str(path).strip()]
        if not normalized_paths:
            return
        self.workspace_revision += 1
        for path in normalized_paths:
            if _is_scratch(path, self._temporary_paths):
                continue
            reason = "newly created non-scratch file" if created else "created or modified final workspace file"
            self._mark_possible(path, reason)
            item = self.deliverables[path]
            item.writes += 1
            item.last_write_revision = self.workspace_revision
            item.reread_after_write = False

    def record_read(self, path: str) -> None:
        normalized = self._normalize_path(path)
        item = self.deliverables.get(normalized)
        if item is None:
            return
        item.last_read_revision = self.workspace_revision
        item.reread_after_write = item.writes > 0 and item.last_read_revision >= item.last_write_revision

    def record_validation(self, command: str, exit_code: int, stdout: str = "", stderr: str = "", *, strength: int = 1, clean: bool = True) -> ValidationRecord:
        reasons = validation_mask_reasons(command)
        record = ValidationRecord(
            command=command,
            normalized_command=normalize_command(command),
            exit_code=exit_code,
            workspace_revision=self.workspace_revision,
            weakened=bool(reasons),
            weakening_reasons=reasons,
            clean=clean,
            strength=strength,
            result_signature=_result_signature(stdout, stderr, exit_code),
        )
        for previous in self.validations:
            similarity = difflib.SequenceMatcher(None, previous.normalized_command, record.normalized_command).ratio()
            if similarity >= 0.9 and previous.workspace_revision == record.workspace_revision and _materially_different(previous.result_signature, record.result_signature):
                previous.unstable = True
                record.unstable = True
        self.validations.append(record)
        self._resolve_issues_after_validation(record)
        self._update_best_state()
        return record

    def _resolve_issues_after_validation(self, validation: ValidationRecord) -> None:
        if validation.exit_code != 0 or validation.weakened:
            return
        for issue in self.issues:
            if not issue.resolved and self.workspace_revision > issue.identified_revision:
                issue.resolved = True
                issue.resolution = f"workspace changed and later clean validation passed: {validation.command}"

    def _score(self) -> tuple[int, int, int, int, int]:
        strong_passes = sum(v.exit_code == 0 and not v.weakened and not v.unstable and v.clean and v.workspace_revision == self.workspace_revision for v in self.validations)
        failed = sum(v.exit_code != 0 for v in self.validations)
        unresolved = sum(not issue.resolved for issue in self.issues)
        verified = sum(item.reread_after_write for item in self.deliverables.values() if item.writes)
        contaminated = sum(not v.clean for v in self.validations)
        return (strong_passes, verified, -unresolved, -contaminated, -failed)

    def _update_best_state(self) -> None:
        score = self._score()
        if self.last_known_better is None or tuple(self.last_known_better["score"]) < score:
            self.last_known_better = {"workspace_revision": self.workspace_revision, "score": list(score), "validation_count": len(self.validations)}

    def current_weaker_than_best(self) -> bool:
        return self.last_known_better is not None and self._score() < tuple(self.last_known_better["score"])

    def classify(self, check_text: str, *, final_claim_relies_on_favourable_run: bool = False) -> dict[str, Any]:
        unread = sorted(item.path for item in self.deliverables.values() if item.writes and not item.reread_after_write)
        unresolved = [issue.summary for issue in self.issues if not issue.resolved]
        unstable = [v.command for v in self.validations if v.unstable]
        latest = self.validations[-1] if self.validations else None
        strong = [v for v in self.validations if v.exit_code == 0 and not v.weakened and not v.unstable and v.clean and v.strength >= 3 and v.workspace_revision == self.workspace_revision]
        high: list[str] = []
        medium: list[str] = []
        if unread:
            high.append("possible required deliverables were not re-read after writing")
        if _WEAK_RE.search(check_text):
            high.append("the consistency response reports weak or unverified requirements")
        if len(check_text.strip()) < 40 or not _EVIDENCE_CLAIM_RE.search(check_text):
            high.append("the agent cannot point to evidence for each explicit requirement")
        if unresolved:
            high.append("self-identified defects remain unresolved")
        if latest and latest.weakened:
            high.append("final validation used masked or partial evidence")
        if latest and latest.exit_code != 0:
            high.append("current validation fails")
        if self.validations and self.validations[-1].workspace_revision < self.workspace_revision:
            medium.append("substantial edits were made after the last validation")
        if unstable and final_claim_relies_on_favourable_run:
            high.append("completion relies on one favourable unstable validation run")
        if not strong:
            if self.validations:
                high.append("strongest completion evidence is only an exit code, filtered output, or assertion")
            else:
                high.append("no direct validation evidence was recorded")
        if _CONTRADICTION_RE.search(check_text) and not re.search(r"\bno\b[^.\n]{0,25}\bcontradict", check_text, re.IGNORECASE):
            high.append("the consistency response reports contradictory evidence")
        if unstable:
            medium.append("materially similar validation evidence is unstable")
        if any(v.weakened for v in self.validations):
            medium.append("validation was filtered, partial, or had weakened exit semantics")
        if self.deliverables and not all(item.reread_after_write for item in self.deliverables.values() if item.writes):
            medium.append("deliverable checking was superficial")
        if self.current_weaker_than_best():
            medium.append("current evidence is weaker than an earlier observed state")
        level = "high" if high else ("medium" if medium else "low")
        reasons = high if high else medium
        result = {"level": level, "reasons": reasons, "high_risk_signals": high, "medium_risk_signals": medium}
        self.current_risk = result
        return result

    def build_model_visible(self, check_text: str, risk: dict[str, Any]) -> str:
        lines = ["Final consistency check:", _cap(check_text.strip(), 1800), f"Risk level: {risk['level']}"]
        gaps = risk.get("reasons") or []
        lines.append("Unresolved gaps: " + ("; ".join(gaps[:5]) if gaps else "none identified"))
        if risk["level"] != "low":
            lines.append("Next action: address the highest-risk gap and rerun the strongest unmasked validation.")
        else:
            lines.append("Next action: provide the concise final response.")
        return _cap("\n".join(lines), MAX_FINAL_CHECK_MODEL_VISIBLE_CHARS)

    def record_check(self, full_text: str, risk: dict[str, Any]) -> str:
        visible = self.build_model_visible(full_text, risk)
        self.checks.append({"full_response": full_text, "model_visible_response": visible, "risk": risk})
        return visible

    def artifacts(self) -> dict[str, dict[str, Any]]:
        return {
            "final_consistency_check.json": {"checks": self.checks, "max_model_visible_chars": MAX_FINAL_CHECK_MODEL_VISIBLE_CHARS},
            "completion_risk.json": {**self.current_risk, "high_risk_warning_count": self.high_risk_warning_count},
            "required_deliverable_tracking.json": {"workspace_revision": self.workspace_revision, "deliverables": [asdict(value) for value in self.deliverables.values()]},
            "validation_evidence_history.json": {"validations": [asdict(value) for value in self.validations]},
            "unresolved_issues.json": {"issues": [asdict(value) for value in self.issues]},
            "last_known_better_state.json": {"best": self.last_known_better, "current_score": list(self._score()), "current_weaker": self.current_weaker_than_best()},
        }

    def write_artifacts(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, payload in self.artifacts().items():
            (directory / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
