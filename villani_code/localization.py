from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class LocalizationResult:
    target_files: list[str] = field(default_factory=list)
    likely_bug_class: str = "unknown"
    repair_intent: str = ""
    confidence: float = 0.4
    evidence: list[str] = field(default_factory=list)
    suggested_validation_commands: list[str] = field(default_factory=list)


class LocalizationEngine:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def localize_from_goal(self, goal: str, repo_signals: dict[str, Any] | None = None) -> LocalizationResult:
        goal_lower = goal.lower()
        ranked = self.rank_candidate_files(goal)
        bug_class = self.derive_bug_class(goal)
        tests = [p for p in ranked if p.startswith("tests/")][:2]
        cmds = self.recommend_validation_commands(ranked, repo_signals or {})
        return LocalizationResult(
            target_files=ranked[:6],
            likely_bug_class=bug_class,
            repair_intent="identify likely implementation location and minimal fix path",
            confidence=0.7 if ranked else 0.35,
            evidence=[f"goal_tokens={len(goal_lower.split())}", f"ranked_candidates={len(ranked)}"],
            suggested_validation_commands=cmds + ([f"pytest -q {' '.join(tests)}"] if tests else []),
        )

    def localize_from_failure_output(self, stdout: str, stderr: str, repo_signals: dict[str, Any] | None = None) -> LocalizationResult:
        combined = f"{stdout}\n{stderr}"[:6000]
        file_hits = re.findall(r"([\w./-]+\.py)", combined)
        ranked = self.rank_candidate_files(" ".join(file_hits))
        trace = re.search(r'File "([^"]+)", line (\d+)', combined)
        evidence = []
        if trace:
            evidence.append(f"traceback={trace.group(1)}:{trace.group(2)}")
        bug_class = self.derive_bug_class(combined)
        return LocalizationResult(
            target_files=(list(dict.fromkeys(file_hits)) + ranked)[:8],
            likely_bug_class=bug_class,
            repair_intent="repair failing path observed in command output",
            confidence=0.75 if file_hits else 0.45,
            evidence=evidence,
            suggested_validation_commands=self.recommend_validation_commands(file_hits or ranked, repo_signals or {}),
        )

    def localize_from_diff(self, diff_text: str, repo_signals: dict[str, Any] | None = None) -> LocalizationResult:
        changed = re.findall(r"^\+\+\+ b/(.+)$", diff_text, flags=re.MULTILINE)
        bug_class = "regression_containment"
        evidence = [f"changed_files={len(changed)}"]
        return LocalizationResult(
            target_files=list(dict.fromkeys(changed))[:12],
            likely_bug_class=bug_class,
            repair_intent="contain regression fallout around changed files",
            confidence=0.8 if changed else 0.3,
            evidence=evidence,
            suggested_validation_commands=self.recommend_validation_commands(changed, repo_signals or {}),
        )

    def rank_candidate_files(self, signal_text: str) -> list[str]:
        terms = [t for t in re.findall(r"[a-zA-Z_]{3,}", signal_text.lower()) if t not in {"the", "with", "from", "this", "that"}]
        scored: dict[str, int] = {}
        for path in self.repo_root.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel = path.relative_to(self.repo_root).as_posix()
            if any(part.startswith(".") and part != ".github" for part in path.parts):
                continue
            score = 0
            name = path.name.lower()
            for term in terms:
                if term in rel.lower():
                    score += 3
                if term in name:
                    score += 2
            if rel.startswith("tests/"):
                score -= 1
            if score > 0:
                scored[rel] = score
        return [k for k, _ in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)]

    def derive_bug_class(self, text: str) -> str:
        low = text.lower()
        if "import" in low and "error" in low:
            return "import_error"
        if "typeerror" in low or "attributeerror" in low:
            return "type_contract"
        if "assert" in low or "failed" in low or "regression" in low:
            return "behavior_regression"
        if "timeout" in low or "slow" in low:
            return "performance"
        return "logic_bug"

    def recommend_validation_commands(self, target_files: list[str], repo_signals: dict[str, Any]) -> list[str]:
        cmds: list[str] = []
        if any(str(p).startswith("tests/") for p in target_files):
            tests = [p for p in target_files if str(p).startswith("tests/")][:3]
            cmds.append("pytest -q " + " ".join(tests))
        elif any(str(p).endswith(".py") for p in target_files):
            cmds.append("pytest -q")
        cmds.extend([str(c) for c in repo_signals.get("likely_validation_commands", [])[:2]])
        return list(dict.fromkeys([c.strip() for c in cmds if c.strip()]))
