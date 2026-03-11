from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from villani_code.benchmark.models import TaskFamily
from villani_code.localize.ranker import rank_suspects
from villani_code.runtime.blackboard import BlackboardStore
from villani_code.runtime.budgets import select_runtime_budgets, timeout_imminent
from villani_code.runtime.schemas import Blackboard, BranchRecord, Evidence, StopReason
from villani_code.runtime.trace import emit_runtime_event
from villani_code.search.frontier import BranchFrontier, FrontierBranch
from villani_code.search.pruning import no_progress_stop, should_prune_branch


class WeakSearchController:
    def __init__(self, runner: Any, instruction: str, timeout_seconds: float = 300.0) -> None:
        self.runner = runner
        self.repo: Path = runner.repo
        self.instruction = instruction
        self.timeout_seconds = timeout_seconds
        self.started = time.monotonic()

    def _collect_evidence(self) -> Evidence:
        config = self.runner.benchmark_config
        evidence = Evidence(
            visible_verification_commands=list(config.visible_verification),
            hidden_verification_commands=list(config.hidden_verification),
            benchmark_expected_files=list(config.expected_files),
            benchmark_allowlist_paths=list(config.allowlist_paths),
        )
        if self.runner.benchmark_config.task_id and "repro" in self.runner.benchmark_config.task_id:
            evidence.repro_commands = list(config.visible_verification)
        return evidence

    def run(self) -> dict[str, Any]:
        config = self.runner.benchmark_config
        budgets = select_runtime_budgets(config, max_files_touched=config.max_files_touched)
        run_id = uuid.uuid4().hex[:12]
        board = Blackboard(
            run_id=run_id,
            task_id=config.task_id or "adhoc",
            objective=self.instruction,
            repo_root=str(self.repo),
            budgets=budgets,
            evidence=self._collect_evidence(),
            constraints={"max_minutes": getattr(config, "max_minutes", None)},
        )
        store = BlackboardStore(self.repo, run_id)
        store.write(board)
        emit_runtime_event(self.repo, self.runner.event_callback, "weak_search_started", run_id=run_id, task_id=board.task_id)

        frontier = BranchFrontier()
        no_improve = 0
        best_score = 0.0
        branches_pruned = 0

        for cycle in range(1, budgets.max_cycles + 1):
            board.cycle = cycle
            emit_runtime_event(self.repo, self.runner.event_callback, "weak_search_cycle_started", cycle=cycle)
            if timeout_imminent(self.started, time.monotonic(), self.timeout_seconds, avg_cycle_seconds=8.0):
                board.final_result = {"stop_reason": StopReason.TIMEOUT_IMMINENT.value, "best_patch_score": best_score}
                break

            candidates = list(dict.fromkeys(config.expected_files + config.allowlist_paths))[:8]
            board.suspects = rank_suspects(self.repo, board.evidence, candidates)[:2]
            emit_runtime_event(self.repo, self.runner.event_callback, "suspects_ranked", count=len(board.suspects))
            improved = False
            for suspect in board.suspects:
                from villani_code.hypothesize.generator import generate_hypotheses
                kept, rejected = generate_hypotheses(suspect, self.instruction, budgets.max_hypotheses_per_suspect)
                board.hypotheses.extend(kept + rejected)
                emit_runtime_event(self.repo, self.runner.event_callback, "hypotheses_generated", suspect=suspect.file, kept=len(kept), rejected=len(rejected))
                for hyp in kept[: budgets.max_candidates_per_hypothesis]:
                    branch_id = f"br-{len(board.branches)+1}"
                    frontier.add(FrontierBranch(id=branch_id, suspect_ref=suspect.file, hypothesis_id=hyp.id))
                    board.branches.append(BranchRecord(id=branch_id, suspect_ref=suspect.file, hypothesis_id=hyp.id))

            for branch in frontier.top_active(2):
                branch.attempts += 1
                if should_prune_branch(branch, repeated_signature="stale"):
                    branches_pruned += 1
                    emit_runtime_event(self.repo, self.runner.event_callback, "branch_pruned", branch_id=branch.id)
                    continue
                branch.best_score = max(branch.best_score, 0.3)
                if branch.best_score > best_score:
                    best_score = branch.best_score
                    improved = True

            no_improve = 0 if improved else no_improve + 1
            if no_progress_stop(no_improve, budgets.max_consecutive_no_improvement_cycles):
                board.final_result = {"stop_reason": StopReason.NO_PROGRESS.value, "best_patch_score": best_score}
                break
            store.write(board)

        if not board.final_result:
            board.final_result = {"stop_reason": StopReason.EXHAUSTED_BUDGET.value, "best_patch_score": best_score}
        store.write(board)
        summary = {
            "weak_search_cycles": board.cycle,
            "branches_created": len(board.branches),
            "branches_pruned": branches_pruned,
            "hypotheses_generated": len(board.hypotheses),
            "candidate_patches_generated": 0,
            "candidate_patches_verified": 0,
            "scope_expansions": 0,
            "no_progress_stop": board.final_result.get("stop_reason") == StopReason.NO_PROGRESS.value,
            "best_patch_score": best_score,
            "stop_reason": board.final_result.get("stop_reason"),
        }
        store.write_summary(summary)
        emit_runtime_event(self.repo, self.runner.event_callback, "weak_search_stopped", **summary)
        return {"response": {"role": "assistant", "content": [{"type": "text", "text": "Weak-search runtime completed."}]}, "weak_search": summary}
