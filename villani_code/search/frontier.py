from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FrontierBranch:
    id: str
    suspect_ref: str
    hypothesis_id: str
    best_score: float = 0.0
    scope_level: str = "symbol"
    attempts: int = 0
    failure_signatures: list[str] = field(default_factory=list)


class BranchFrontier:
    def __init__(self) -> None:
        self.branches: list[FrontierBranch] = []

    def add(self, branch: FrontierBranch) -> None:
        self.branches.append(branch)

    def top_active(self, n: int = 2) -> list[FrontierBranch]:
        return sorted(self.branches, key=lambda b: b.best_score, reverse=True)[:n]
