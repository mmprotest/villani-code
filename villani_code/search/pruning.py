from __future__ import annotations

from villani_code.search.frontier import FrontierBranch


def should_prune_branch(branch: FrontierBranch, repeated_signature: str | None = None) -> bool:
    if repeated_signature:
        branch.failure_signatures.append(repeated_signature)
    if len(branch.failure_signatures) >= 2 and len(set(branch.failure_signatures[-2:])) == 1:
        return True
    if branch.attempts >= 2 and branch.best_score < 0.2:
        return True
    return False


def no_progress_stop(consecutive_no_improvement_cycles: int, max_allowed: int = 2) -> bool:
    return consecutive_no_improvement_cycles >= max_allowed
