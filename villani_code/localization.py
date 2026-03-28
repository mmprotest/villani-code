from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STOP_WORDS = {"the", "with", "from", "this", "that", "when", "into", "about", "and", "for"}


@dataclass(slots=True)
class LocalizationEvidence:
    evidence_type: str
    source: str
    detail: str
    weight: float


@dataclass(slots=True)
class RankedLocalizationCandidate:
    file_path: str
    score: float
    evidence: list[LocalizationEvidence] = field(default_factory=list)


@dataclass(slots=True)
class LocalizationResult:
    target_files: list[str] = field(default_factory=list)
    likely_bug_class: str = "unknown"
    repair_intent: str = ""
    confidence: float = 0.4
    evidence: list[str] = field(default_factory=list)
    suggested_validation_commands: list[str] = field(default_factory=list)
    ranked_candidates: list[RankedLocalizationCandidate] = field(default_factory=list)


class LocalizationEngine:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def localize_from_goal(self, goal: str, repo_signals: dict[str, Any] | None = None, structured_signals: dict[str, Any] | None = None) -> LocalizationResult:
        ranked = self.rank_candidate_files(goal, repo_signals=repo_signals, structured_signals=structured_signals)
        bug_class = self.derive_bug_class(goal)
        top = ranked[:6]
        return LocalizationResult(
            target_files=[c.file_path for c in top],
            likely_bug_class=bug_class,
            repair_intent=self._derive_repair_intent(goal, bug_class, top),
            confidence=self._confidence_from_ranked(top),
            evidence=self._result_evidence(top),
            suggested_validation_commands=self.recommend_validation_commands([c.file_path for c in top], repo_signals or {}),
            ranked_candidates=ranked[:12],
        )

    def localize_from_failure_output(self, stdout: str, stderr: str, repo_signals: dict[str, Any] | None = None, structured_signals: dict[str, Any] | None = None) -> LocalizationResult:
        combined = f"{stdout}\n{stderr}"[:7000]
        file_hits = list(dict.fromkeys(re.findall(r"([\w./-]+\.py)", combined)))
        seed = " ".join(file_hits + self._extract_trace_symbols(combined))
        ranked = self.rank_candidate_files(seed, failure_output=combined, repo_signals=repo_signals, structured_signals=structured_signals)
        trace = re.search(r'File "([^"]+)", line (\d+)', combined)
        direct_trace = [trace.group(1)] if trace else []
        merged = list(dict.fromkeys(direct_trace + file_hits + [c.file_path for c in ranked]))[:10]
        bug_class = self.derive_bug_class(combined)
        return LocalizationResult(
            target_files=merged,
            likely_bug_class=bug_class,
            repair_intent=self._derive_repair_intent(combined, bug_class, ranked[:6]),
            confidence=max(0.45, self._confidence_from_ranked(ranked[:6])),
            evidence=self._result_evidence(ranked[:6]) + ([f"traceback={trace.group(1)}:{trace.group(2)}"] if trace else []),
            suggested_validation_commands=self.recommend_validation_commands(merged, repo_signals or {}, failure_output=combined),
            ranked_candidates=ranked[:12],
        )

    def localize_from_diff(self, diff_text: str, repo_signals: dict[str, Any] | None = None) -> LocalizationResult:
        changed = re.findall(r"^\+\+\+ b/(.+)$", diff_text, flags=re.MULTILINE)
        ranked = self.rank_candidate_files(" ".join(changed), repo_signals=repo_signals)
        bug_class = "regression_containment"
        top = list(dict.fromkeys(changed + [c.file_path for c in ranked]))[:12]
        return LocalizationResult(
            target_files=top,
            likely_bug_class=bug_class,
            repair_intent="contain regression fallout around changed files and direct dependents",
            confidence=0.82 if changed else 0.35,
            evidence=[f"changed_files={len(changed)}"] + self._result_evidence(ranked[:5]),
            suggested_validation_commands=self.recommend_validation_commands(top, repo_signals or {}),
            ranked_candidates=ranked[:12],
        )

    def rank_candidate_files(
        self,
        signal_text: str,
        failure_output: str = "",
        repo_signals: dict[str, Any] | None = None,
        structured_signals: dict[str, Any] | None = None,
    ) -> list[RankedLocalizationCandidate]:
        terms = [t for t in re.findall(r"[a-zA-Z_]{3,}", signal_text.lower()) if t not in STOP_WORDS]
        mentioned_paths = set(re.findall(r"([\w./-]+\.(?:py|toml|yaml|yml|json|ini|cfg))", signal_text + "\n" + failure_output))
        test_refs = set(re.findall(r"(?:tests?/[^\s:]+\.py)", signal_text + "\n" + failure_output))
        symbol_refs = set(self._extract_trace_symbols(signal_text + "\n" + failure_output))
        repo_signals = repo_signals or {}
        structured_signals = structured_signals or {}
        command_targets = set(re.findall(r"([\w./-]+\.py(?:::[\w\[\]-]+)?)", " ".join(str(c) for c in structured_signals.get("validation_commands", []))))
        changed_files = {str(p) for p in structured_signals.get("changed_files", []) if str(p)}
        failed_commands = [str(c).lower() for c in structured_signals.get("failed_commands", [])]
        failure_hint_text = " ".join(failed_commands)

        candidates: list[RankedLocalizationCandidate] = []
        for path in self.repo_root.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            if any(part.startswith(".") and part != ".github" for part in path.parts):
                continue
            rel = path.relative_to(self.repo_root).as_posix()
            name = path.name.lower()
            rel_low = rel.lower()
            evidence: list[LocalizationEvidence] = []
            score = 0.0

            if rel in mentioned_paths or rel_low in {p.lower() for p in mentioned_paths}:
                evidence.append(LocalizationEvidence("explicit_path", "signal", rel, 1.7))
                score += 1.7
            for term in terms[:40]:
                if term in rel_low:
                    evidence.append(LocalizationEvidence("path_term", "signal", term, 0.32))
                    score += 0.32
                if term in name:
                    evidence.append(LocalizationEvidence("filename_term", "signal", term, 0.22))
                    score += 0.22
            if symbol_refs and any(sym in rel_low for sym in symbol_refs):
                evidence.append(LocalizationEvidence("trace_symbol", "failure_output", ",".join(sorted(symbol_refs)[:4]), 0.8))
                score += 0.8
            if rel in test_refs:
                evidence.append(LocalizationEvidence("test_reference", "failure_output", rel, 1.1))
                score += 1.1
            elif any(str(ref).endswith(path.name) for ref in test_refs):
                evidence.append(LocalizationEvidence("test_proximity", "failure_output", path.name, 0.5))
                score += 0.5

            if rel in changed_files:
                evidence.append(LocalizationEvidence("recent_change", "structured_execution", rel, 0.42))
                score += 0.42
            if rel in {target.split("::", 1)[0] for target in command_targets}:
                evidence.append(LocalizationEvidence("validation_target", "structured_execution", rel, 0.65))
                score += 0.65
            if any(token in rel_low for token in re.findall(r"[a-z_]{4,}", failure_hint_text)[:20]):
                evidence.append(LocalizationEvidence("failed_command_term", "structured_execution", path.name, 0.25))
                score += 0.25

            if rel.startswith("tests/"):
                score -= 0.1
            if rel.startswith(tuple(repo_signals.get("likely_source_roots", []))):
                evidence.append(LocalizationEvidence("source_root", "repo_signals", rel.split("/", 1)[0], 0.2))
                score += 0.2
            if path.suffix == ".py":
                score += 0.05

            if score > 0.35:
                candidates.append(RankedLocalizationCandidate(file_path=rel, score=round(score, 3), evidence=evidence[:8]))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def derive_bug_class(self, text: str) -> str:
        low = text.lower()
        if "modulenotfounderror" in low or ("import" in low and "error" in low):
            return "import_error"
        if "keyerror" in low or "valueerror" in low:
            return "data_contract"
        if "typeerror" in low or "attributeerror" in low:
            return "type_contract"
        if "assert" in low or "failed" in low or "regression" in low:
            return "behavior_regression"
        if "timeout" in low or "slow" in low or "deadline" in low:
            return "performance"
        if "cli" in low or "argument" in low:
            return "cli_contract"
        return "logic_bug"

    def recommend_validation_commands(self, target_files: list[str], repo_signals: dict[str, Any], failure_output: str = "") -> list[str]:
        cmds: list[str] = []
        tests = [p for p in target_files if str(p).startswith(("tests/", "test/"))][:3]
        if tests:
            cmds.append("pytest -q " + " ".join(tests))
        related_tests = [f"tests/test_{Path(p).stem}.py" for p in target_files if p.endswith(".py") and not p.startswith("tests/")][:2]
        if related_tests:
            cmds.append("pytest -q " + " ".join(related_tests))
        if "pytest" in failure_output or any(p.endswith(".py") for p in target_files):
            cmds.append("pytest -q")
        cmds.extend([str(c) for c in repo_signals.get("likely_validation_commands", [])[:3]])
        return list(dict.fromkeys([c.strip() for c in cmds if c.strip()]))[:4]

    def _extract_trace_symbols(self, text: str) -> list[str]:
        symbols = re.findall(r"(?:in|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        return [s.lower() for s in symbols[:12]]

    def _derive_repair_intent(self, text: str, bug_class: str, ranked: list[RankedLocalizationCandidate]) -> str:
        primary = ranked[0].file_path if ranked else "core implementation path"
        if bug_class == "import_error":
            return f"repair import wiring and module exposure around {primary}"
        if bug_class == "behavior_regression":
            return f"restore expected behavior and assertions near {primary}"
        if bug_class == "cli_contract":
            return f"align CLI argument handling and validation near {primary}"
        if bug_class == "performance":
            return f"reduce hot-path overhead and validate constraints near {primary}"
        return f"apply minimal targeted logic fix near {primary}"

    def _result_evidence(self, ranked: list[RankedLocalizationCandidate]) -> list[str]:
        evidence: list[str] = []
        for candidate in ranked[:4]:
            if not candidate.evidence:
                continue
            top = candidate.evidence[0]
            evidence.append(f"{candidate.file_path}:{top.evidence_type}:{top.detail}:w={top.weight}")
        return evidence

    def _confidence_from_ranked(self, ranked: list[RankedLocalizationCandidate]) -> float:
        if not ranked:
            return 0.3
        top = ranked[0].score
        second = ranked[1].score if len(ranked) > 1 else 0.0
        gap = max(0.0, top - second)
        if top >= 2.6 and gap >= 0.6:
            return 0.9
        if top >= 1.8:
            return 0.78
        if top >= 1.2:
            return 0.62
        return 0.48
