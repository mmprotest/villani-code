from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1

from villani_code.repo_map import RepoMap


@dataclass(slots=True)
class OpportunityCandidate:
    id: str
    title: str
    category: str
    rationale: str
    evidence: list[str]
    confidence: float
    estimated_risk: float
    estimated_value: float
    validation_strategy: list[str]
    followup_candidates: list[str] = field(default_factory=list)
    proposed_next_action: str = ""
    task_contract: str = "inspection"
    affected_files: list[str] = field(default_factory=list)


class OpportunityEngine:
    def __init__(self, repo_map: RepoMap):
        self.repo_map = repo_map

    def generate(self) -> list[OpportunityCandidate]:
        out: list[OpportunityCandidate] = []
        out.extend(self._validation_opportunities())
        out.extend(self._investigation_opportunities())
        out.extend(self._improvement_opportunities())
        return sorted(out, key=lambda c: (c.estimated_value * 0.6 + c.confidence * 0.4), reverse=True)

    def _validation_opportunities(self) -> list[OpportunityCandidate]:
        ops: list[OpportunityCandidate] = []
        if self.repo_map.key_modules:
            ops.append(
                self._mk(
                    "Validate baseline importability",
                    "validation",
                    "Python modules detected; import validation establishes execution baseline.",
                    self.repo_map.key_modules[:3],
                    0.82,
                    0.2,
                    0.8,
                    ["python -c 'import importlib; importlib.invalidate_caches()'"],
                    ["Run baseline tests", "Validate CLI entrypoints"],
                    "inspect package import paths and run a bounded import command",
                    "validation",
                )
            )
        if self.repo_map.tests:
            ops.append(
                self._mk(
                    "Run baseline tests",
                    "validation",
                    "Repository has test files; baseline test execution validates repo health.",
                    self.repo_map.tests[:4],
                    0.85,
                    0.25,
                    0.9,
                    ["pytest -q"],
                    ["Inspect hotspot for improvement"],
                    "run baseline test command with bounded scope",
                    "validation",
                )
            )
        if self.repo_map.entrypoints:
            ops.append(
                self._mk(
                    "Validate CLI entrypoints",
                    "validation",
                    "CLI entrypoint-like files discovered.",
                    self.repo_map.entrypoints[:3],
                    0.74,
                    0.2,
                    0.73,
                    ["python -m <entrypoint> --help"],
                    ["Inspect docs/code CLI mismatch"],
                    "run --help for discovered entrypoint",
                    "validation",
                )
            )
        if self.repo_map.doc_commands:
            ops.append(
                self._mk(
                    "Validate documented commands",
                    "validation",
                    "Docs include executable commands/examples.",
                    self.repo_map.doc_commands[:3],
                    0.72,
                    0.3,
                    0.78,
                    [self.repo_map.doc_commands[0]],
                    ["Investigate docs/code mismatch"],
                    "execute one bounded command sampled from docs",
                    "validation",
                )
            )
        return ops

    def _investigation_opportunities(self) -> list[OpportunityCandidate]:
        ops: list[OpportunityCandidate] = []
        if self.repo_map.todo_hits:
            ops.append(
                self._mk(
                    "Inspect TODO hotspots",
                    "investigation",
                    "TODO/FIXME/HACK markers suggest unresolved work.",
                    self.repo_map.todo_hits[:4],
                    0.6,
                    0.15,
                    0.66,
                    ["locate TODO context and assess actionability"],
                    ["Fix highest-signal TODO"],
                    "inspect highest-signal TODO and classify as bug/doc/debt",
                )
            )
        if self.repo_map.import_hotspots:
            ops.append(
                self._mk(
                    "Inspect import graph hotspots",
                    "investigation",
                    "High import-density modules may hide fragile coupling.",
                    self.repo_map.import_hotspots[:3],
                    0.56,
                    0.1,
                    0.62,
                    ["inspect hotspot module for validation path"],
                    ["Add targeted tests around hotspot"],
                    "inspect top import hotspot module and map missing validation",
                )
            )
        if self.repo_map.docs and self.repo_map.key_modules:
            ops.append(
                self._mk(
                    "Investigate docs/code mismatch",
                    "investigation",
                    "Docs and executable surfaces both exist; mismatch risk is nontrivial.",
                    self.repo_map.docs[:2] + self.repo_map.entrypoints[:1],
                    0.58,
                    0.1,
                    0.65,
                    ["cross-check one docs command against entrypoint"],
                    ["Fix docs command mismatch"],
                    "compare quickstart command with actual CLI/module structure",
                )
            )
        return ops

    def _improvement_opportunities(self) -> list[OpportunityCandidate]:
        ops: list[OpportunityCandidate] = []
        if self.repo_map.tests and self.repo_map.todo_hits:
            ops.append(
                self._mk(
                    "Add targeted regression test for hotspot",
                    "improvement",
                    "Tests exist and TODO hotspot detected; narrow test can harden behavior.",
                    self.repo_map.tests[:2] + self.repo_map.todo_hits[:1],
                    0.52,
                    0.45,
                    0.68,
                    ["run targeted pytest file"],
                    ["Run baseline tests"],
                    "add minimal targeted test around validated hotspot",
                    "effectful",
                )
            )
        return ops

    def _mk(
        self,
        title: str,
        category: str,
        rationale: str,
        evidence: list[str],
        confidence: float,
        risk: float,
        value: float,
        strategy: list[str],
        followups: list[str],
        next_action: str,
        task_contract: str = "inspection",
    ) -> OpportunityCandidate:
        short_evidence = [e for e in evidence if e][:5]
        digest = sha1(f"{title}|{category}|{'|'.join(short_evidence)}".encode()).hexdigest()[:10]
        return OpportunityCandidate(
            id=f"op-{digest}",
            title=title,
            category=category,
            rationale=rationale,
            evidence=short_evidence,
            confidence=round(confidence, 2),
            estimated_risk=round(risk, 2),
            estimated_value=round(value, 2),
            validation_strategy=strategy,
            followup_candidates=followups,
            proposed_next_action=next_action,
            task_contract=task_contract,
            affected_files=[e.split(":", 1)[0] for e in short_evidence if "/" in e],
        )
