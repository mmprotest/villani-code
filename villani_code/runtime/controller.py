from __future__ import annotations
import time
import uuid
from pathlib import Path
from typing import Any
from villani_code.localize.ranker import rank_suspects
from villani_code.runtime.blackboard import BlackboardStore
from villani_code.runtime.budgets import select_runtime_budgets, timeout_imminent
from villani_code.runtime.candidate_executor import CandidateExecutionResult, CandidateExecutor, WeakSearchSessionContext
from villani_code.runtime.policy import RuntimeStrategy, WeakSearchPolicyProfile, decide_runtime_policy, is_direct_repair_profile
from villani_code.runtime.schemas import AttemptRecord, Blackboard, BranchRecord, Evidence, StopReason
from villani_code.runtime.trace import emit_runtime_event
from villani_code.search.frontier import BranchFrontier, FrontierBranch
from villani_code.search.pruning import no_progress_stop, should_prune_branch
from villani_code.synthesize.edit_budget import EditBudget
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
            failing_tests=[self.instruction],
            error_messages=[self.instruction],
            visible_verification_commands=list(config.visible_verification),
            hidden_verification_commands=list(config.hidden_verification),
            benchmark_expected_files=list(config.expected_files),
            benchmark_allowlist_paths=list(config.allowlist_paths),
        )
        if config.task_id and "repro" in config.task_id:
            evidence.repro_commands = list(config.visible_verification)
        return evidence
    def _candidate_pool(self, config: Any) -> list[str]:
        if config.enabled:
            return list(dict.fromkeys(config.expected_files + config.allowlist_paths))[:12]
        hinted = list(dict.fromkeys(config.allowlist_paths))
        if hinted:
            return hinted[:12]
        repo_files = [p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*") if p.is_file() and ".git/" not in p.as_posix()]
        return repo_files[:20]
    def _record_attempt(self, board: Blackboard, branch_id: str, attempt_id: str, hypothesis_id: str, result: CandidateExecutionResult, hypothesis_source: str) -> AttemptRecord:
        attempt = AttemptRecord(
            id=attempt_id,
            branch_id=branch_id,
            files_touched=result.changed_files,
            changed_line_count=int(result.diff_stats.get("changed_line_count", 0)),
            hard_fail=result.hard_fail,
            reason=result.attempt_summary or "candidate_failed",
            result="passed" if result.success else "failed",
            score=result.score,
            verifier_outputs={
                **result.verification_outputs,
                "score_breakdown": result.score_breakdown,
                "verification_stage": result.verification_stage,
                "target_verification_passed": result.target_verification_passed,
                "collateral_verification_passed": result.collateral_verification_passed,
                "static_sanity_passed": result.static_sanity_passed,
                "minimality_score": result.minimality_score,
                "novelty_score": result.novelty_score,
                "target_exit_codes": result.target_exit_codes,
                "target_command_count": result.target_command_count,
                "workspace_prep_seconds": result.workspace_prep_seconds,
                "prompt_build_seconds": result.prompt_build_seconds,
                "model_execution_seconds": result.model_execution_seconds,
                "tool_execution_seconds": result.tool_execution_seconds,
                "verification_seconds": result.verification_seconds,
                "candidate_total_seconds": result.candidate_total_seconds,
                "workspace_strategy": result.workspace_strategy,
                "policy_profile": result.policy_profile,
                "direct_repair_attempted": result.direct_repair_attempted,
                "direct_patch_target_file": result.direct_repair_suspect,
                "hypothesis_stage_skipped_initially": result.hypothesis_stage_skipped_initially,
                "escalated_after_direct_failure": result.escalation_occurred,
                "session_context_reused": result.session_context_reused,
                "escalation_occurred": result.escalation_occurred,
                "escalation_reason": result.escalation_reason,
                "prompt_tokens_first_attempt": result.prompt_tokens_first_attempt,
                "tool_calls_first_attempt": result.tool_calls_first_attempt,
                "exploration_block_triggered": result.exploration_block_triggered,
            },
            attempt_category=result.attempt_category,
            blocked_reason=result.blocked_reason,
            patch_artifact_path=result.patch_artifact_path,
            failure_signature=result.failure_signature,
            hypothesis_id=hypothesis_id,
            prompt_summary=result.prompt_summary,
            hypothesis_source=hypothesis_source,
        )
        board.attempts.append(attempt)
        return attempt
    def _select_direct_repair_target(self, suspects: list[Any], evidence: Evidence, config: Any) -> tuple[str, str]:
        if len(config.expected_files) == 1:
            return str(config.expected_files[0]), "expected_single_implementation_file"
        stack = "\n".join([*evidence.stack_traces, *evidence.error_messages])
        for suspect in suspects:
            if not suspect.file:
                continue
            if suspect.file.startswith("tests/") and any(not s.file.startswith("tests/") for s in suspects):
                continue
            if suspect.file in stack:
                return suspect.file, "stacktrace_or_error_path_match"
        impl_suspects = [s for s in suspects if s.file and not s.file.startswith("tests/")]
        if impl_suspects:
            return impl_suspects[0].file, "strongest_lexical_impl_overlap"
        if suspects:
            return suspects[0].file, "top_ranked_suspect"
        return "", "no_suspect_available"
    def _should_escalate_after_direct_attempt(self, result: CandidateExecutionResult, suspect: str) -> tuple[bool, str]:
        if result.success:
            return False, "solved"
        meaningful_patch = bool(result.changed_files and result.diff_text.strip())
        verification_improved = bool(result.target_verification_passed) or bool(result.score > 0.35)
        if result.blocked_reason.startswith("direct_repair_thrash"):
            return True, "direct_mode_exploration_thrash"
        if meaningful_patch and not result.target_verification_passed:
            return True, "meaningful_patch_failed_target_verification"
        if result.attempt_category == "rejected_noop" and suspect:
            return True, "noop_after_target_inspection"
        if verification_improved:
            return True, "partial_progress_requires_guided_search"
        return False, "direct_repair_insufficient_signal"
    def _runtime_task_family(self, config: Any) -> str | None:
        return (getattr(config, "task_family", None) or "").strip() or None
    def _runtime_task_type(self, config: Any) -> str | None:
        return (getattr(config, "task_type", None) or "").strip() or None
    def _run_direct_patch_attempt(
        self,
        *,
        board: Blackboard,
        executor: CandidateExecutor,
        decision: Any,
        suspects: list[Any],
        constraints: dict[str, Any],
        config: Any,
        session_context: WeakSearchSessionContext,
    ) -> tuple[CandidateExecutionResult, str, str]:
        suspect_file, target_reason = self._select_direct_repair_target(suspects, board.evidence, config)
        board.decision_log.append({"event": "target_selected", "target_file": suspect_file, "target_selection_reason": target_reason})
        board.decision_log.append({"event": "direct_repair_attempted", "suspect": suspect_file})
        attempt_id = f"att-{len(board.attempts)+1}"
        result = executor.evaluate_candidate(
            repo_path=self.repo,
            objective=self.instruction,
            suspect_region=suspect_file,
            hypothesis_id="candidate-0",
            hypothesis="Directly repair the likely root bug in the suspect file.",
            constraints=constraints,
            runtime_profile="benchmark" if config.enabled else "interactive",
            benchmark_config=config,
            baseline_handle="clean-copy",
            edit_budget=EditBudget(max_files=1, max_lines=12),
            branch_failure_history=[],
            timeout_budget_seconds=max(10.0, self.timeout_seconds - (time.monotonic() - self.started)),
            attempt_id=attempt_id,
            max_candidate_turns=2,
            max_candidate_tool_calls=4,
            policy_profile=decision.profile.value,
            execution_mode="direct_repair",
            session_context=session_context,
            hypothesis_stage_skipped_initially=True,
        )
        return result, attempt_id, suspect_file
    def run(self) -> dict[str, Any]:
        config = self.runner.benchmark_config
        decision = decide_runtime_policy(
            benchmark_config=config,
            is_interactive=not config.enabled,
            task_family=(config.task_family if config.enabled else None),
            task_type=(config.task_type if config.enabled else None),
            previous_candidate_failed=False,
            no_progress_cycles=0,
            has_stacktrace_or_error=bool(self.instruction.strip()),
        )
        budgets = select_runtime_budgets(config, max_files_touched=config.max_files_touched, policy_profile=decision.profile)
        run_id = uuid.uuid4().hex[:12]
        board = Blackboard(
            run_id=run_id,
            task_id=config.task_id or "adhoc",
            objective=self.instruction,
            repo_root=str(self.repo),
            budgets=budgets,
            evidence=self._collect_evidence(),
            constraints={"max_minutes": getattr(config, "max_minutes", None), "policy_profile": decision.profile.value, "strategy_selected": decision.strategy.value},
        )
        store = BlackboardStore(self.repo, run_id)
        store.write(board)
        board.decision_log.append({"event": "strategy_selected", "strategy_selected": decision.strategy.value, "policy_profile": decision.profile.value, "reason": decision.reason, "direct_repair_first_used": decision.strategy == RuntimeStrategy.DIRECT_REPAIR_FIRST, "initial_hypothesis_stage_skipped": decision.strategy == RuntimeStrategy.DIRECT_REPAIR_FIRST})
        emit_runtime_event(self.repo, self.runner.event_callback, "weak_search_started", run_id=run_id, task_id=board.task_id, policy_profile=decision.profile.value, strategy_selected=decision.strategy.value)
        frontier = BranchFrontier()
        no_improve = 0
        best_score = 0.0
        branches_pruned = 0
        candidates_generated = 0
        candidates_verified = 0
        real_candidate_attempts = 0
        noop_rejections = 0
        blocked_attempts = 0
        verified_attempts = 0
        executor = CandidateExecutor(self.runner, self.instruction, budgets.max_patch_lines, budgets.max_files_per_patch)
        session_context = WeakSearchSessionContext(planning_prompt=self.instruction)
        blocked_reason = ""
        escalation_occurred = False
        fast_path_attempted = decision.strategy == RuntimeStrategy.DIRECT_REPAIR_FIRST
        candidate0_attempted = False
        for cycle in range(1, budgets.max_cycles + 1):
            board.cycle = cycle
            emit_runtime_event(self.repo, self.runner.event_callback, "weak_search_cycle_started", cycle=cycle, policy_profile=decision.profile.value)
            if timeout_imminent(self.started, time.monotonic(), self.timeout_seconds, avg_cycle_seconds=8.0):
                board.final_result = {"stop_reason": StopReason.TIMEOUT_IMMINENT.value, "best_patch_score": best_score}
                break
            suspects = rank_suspects(self.repo, board.evidence, self._candidate_pool(config))
            if is_direct_repair_profile(decision.profile):
                suspects = suspects[:1]
            else:
                suspects = suspects[:2]
            board.suspects = suspects
            emit_runtime_event(self.repo, self.runner.event_callback, "suspects_ranked", count=len(board.suspects))
            improved = False
            cycle_best: tuple[FrontierBranch, CandidateExecutionResult, str, Any] | None = None
            constraints = {
                "allowlist_paths": config.allowlist_paths,
                "forbidden_paths": config.forbidden_paths,
                "expected_files": config.expected_files,
                "max_files_touched": config.max_files_touched,
                "visible_verification": list(config.visible_verification),
            }
            if cycle == 1 and decision.strategy == RuntimeStrategy.DIRECT_REPAIR_FIRST and suspects:
                candidate0_attempted = True
                direct, attempt_id, suspect_file = self._run_direct_patch_attempt(
                    board=board,
                    executor=executor,
                    decision=decision,
                    suspects=suspects,
                    constraints=constraints,
                    config=config,
                    session_context=session_context,
                )
                candidates_generated += 1
                self._record_attempt(board, "candidate-0", attempt_id, "candidate-0", direct, "direct")
                if direct.attempt_category in {"candidate_verified", "verification_failed"}:
                    real_candidate_attempts += 1
                if direct.attempt_category in {"rejected_noop", "rejected_diff_guard", "blocked_timeout", "blocked_runtime_error", "blocked_model_failure", "blocked_policy"}:
                    blocked_attempts += 1
                if direct.success:
                    executor.commit_candidate(self.repo, direct)
                    board.final_result = {"stop_reason": StopReason.SOLVED.value, "best_patch_score": direct.score, "attempt_id": attempt_id, "branch_id": "candidate-0"}
                    best_score = direct.score
                    emit_runtime_event(self.repo, self.runner.event_callback, "candidate_patch_committed", branch_id="candidate-0", attempt_id=attempt_id, patch_artifact=direct.patch_artifact_path)
                    break
                should_escalate, escalate_reason = self._should_escalate_after_direct_attempt(direct, suspect_file)
                board.decision_log.append({"event": "direct_repair_result", "escalate": should_escalate, "reason": escalate_reason})
                if not should_escalate:
                    board.final_result = {"stop_reason": StopReason.NO_PROGRESS.value, "best_patch_score": best_score, "blocked_reason": escalate_reason}
                    break
                decision = decide_runtime_policy(
                    benchmark_config=config,
                    is_interactive=not config.enabled,
                    task_family=self._runtime_task_family(config) if config.enabled else None,
                    task_type=self._runtime_task_type(config) if config.enabled else None,
                    previous_candidate_failed=True,
                    no_progress_cycles=1,
                    has_stacktrace_or_error=True,
                )
                if decision.strategy in {RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE, RuntimeStrategy.FULL_WEAK_SEARCH}:
                    escalation_occurred = True
                    budgets = select_runtime_budgets(config, max_files_touched=config.max_files_touched, policy_profile=decision.profile)
                else:
                    board.final_result = {"stop_reason": StopReason.NO_PROGRESS.value, "best_patch_score": best_score, "blocked_reason": "direct_repair_no_escalation_profile"}
                    break
            for suspect in suspects:
                from villani_code.hypothesize.generator import generate_hypotheses
                kept, rejected, fallback_used = generate_hypotheses(suspect, self.instruction, budgets.max_hypotheses_per_suspect, runner=self.runner)
                board.hypotheses.extend(kept + rejected)
                board.decision_log.append({"event": "hypotheses_generated", "suspect": suspect.file, "fallback": fallback_used})
                emit_runtime_event(self.repo, self.runner.event_callback, "hypotheses_generated", suspect=suspect.file, kept=len(kept), rejected=len(rejected), fallback=fallback_used)
                for hyp in kept[: budgets.max_candidates_per_hypothesis]:
                    branch_id = f"br-{len(board.branches)+1}"
                    frontier.add(FrontierBranch(id=branch_id, suspect_ref=suspect.file, hypothesis_id=hyp.id))
                    board.branches.append(BranchRecord(id=branch_id, suspect_ref=suspect.file, hypothesis_id=hyp.id))
            for branch in frontier.top_active(budgets.max_active_branches):
                branch.attempts += 1
                branch_rec = next((b for b in board.branches if b.id == branch.id), None)
                attempt_id = f"att-{len(board.attempts)+1}"
                hypothesis = next((h for h in board.hypotheses if h.id == branch.hypothesis_id), None)
                if not hypothesis:
                    continue
                candidates_generated += 1
                result = executor.evaluate_candidate(
                    repo_path=self.repo,
                    objective=self.instruction,
                    suspect_region=branch.suspect_ref,
                    hypothesis_id=hypothesis.id,
                    hypothesis=hypothesis.text,
                    constraints=constraints,
                    runtime_profile="benchmark" if config.enabled else "interactive",
                    benchmark_config=config,
                    baseline_handle="clean-copy",
                    edit_budget=EditBudget(max_files=budgets.max_files_per_patch, max_lines=budgets.max_patch_lines),
                    branch_failure_history=list(branch.failure_signatures),
                    timeout_budget_seconds=max(10.0, self.timeout_seconds - (time.monotonic() - self.started)),
                    attempt_id=attempt_id,
                    max_candidate_turns=budgets.max_candidate_turns,
                    max_candidate_tool_calls=budgets.max_candidate_tool_calls,
                    policy_profile=decision.profile.value,
                    execution_mode="heavy",
                    session_context=session_context,
                )
                attempt = self._record_attempt(board, branch.id, attempt_id, hypothesis.id, result, "fallback" if "fallback" in hypothesis.notes else "model")
                if branch_rec:
                    branch_rec.attempts_list.append(attempt.id)
                    branch_rec.best_score = max(branch_rec.best_score, result.score)
                if result.attempt_category in {"rejected_noop", "rejected_diff_guard", "blocked_timeout", "blocked_runtime_error", "blocked_model_failure", "blocked_policy"}:
                    blocked_attempts += 1
                    if result.attempt_category == "rejected_noop":
                        noop_rejections += 1
                if result.attempt_category in {"candidate_verified", "verification_failed"}:
                    real_candidate_attempts += 1
                if result.hard_fail:
                    branch.failure_signatures.append(result.failure_signature or result.blocked_reason)
                    if should_prune_branch(branch, repeated_signature=result.failure_signature or result.blocked_reason):
                        branches_pruned += 1
                        if branch_rec:
                            branch_rec.status = "pruned"
                    continue
                candidates_verified += 1
                verified_attempts += 1
                branch.best_score = max(branch.best_score, result.score)
                if branch.best_score > best_score:
                    best_score = branch.best_score
                    improved = True
                if cycle_best is None or result.score > cycle_best[1].score:
                    cycle_best = (branch, result, attempt_id, hypothesis)
            if cycle_best:
                win_branch, win_result, win_attempt_id, _win_hyp = cycle_best
                if win_result.success:
                    executor.commit_candidate(self.repo, win_result)
                    board.final_result = {"stop_reason": StopReason.SOLVED.value, "best_patch_score": best_score, "attempt_id": win_attempt_id, "branch_id": win_branch.id}
                    break
            no_improve = 0 if improved else no_improve + 1
            if no_progress_stop(no_improve, budgets.max_consecutive_no_improvement_cycles):
                if decision.strategy == RuntimeStrategy.DIRECT_REPAIR_FIRST:
                    decision = decide_runtime_policy(
                        benchmark_config=config,
                        is_interactive=not config.enabled,
                        task_family=self._runtime_task_family(config) if config.enabled else None,
                        task_type=self._runtime_task_type(config) if config.enabled else None,
                        previous_candidate_failed=True,
                        no_progress_cycles=no_improve,
                        has_stacktrace_or_error=True,
                    )
                    if decision.strategy in {RuntimeStrategy.GUIDED_SEARCH_AFTER_FAILURE, RuntimeStrategy.FULL_WEAK_SEARCH}:
                        escalation_occurred = True
                        budgets = select_runtime_budgets(config, max_files_touched=config.max_files_touched, policy_profile=decision.profile)
                        no_improve = 0
                        continue
                board.final_result = {"stop_reason": StopReason.NO_PROGRESS.value, "best_patch_score": best_score}
                break
            store.write(board)
        if not board.final_result:
            if candidates_generated == 0:
                blocked_reason = "no_candidates_evaluated"
                board.final_result = {"stop_reason": StopReason.BLOCKED.value, "best_patch_score": best_score, "blocked_reason": blocked_reason}
            else:
                board.final_result = {"stop_reason": StopReason.EXHAUSTED_BUDGET.value, "best_patch_score": best_score}
        board.run_stats = {
            "real_candidate_attempts": real_candidate_attempts,
            "noop_rejections": noop_rejections,
            "blocked_attempts": blocked_attempts,
            "verified_attempts": verified_attempts,
            "branches_pruned": branches_pruned,
            "stop_reason": board.final_result.get("stop_reason"),
            "policy_profile": decision.profile.value,
            "strategy_selected": decision.strategy.value,
            "fast_path_attempted": fast_path_attempted,
            "candidate_0_attempted": candidate0_attempted,
            "escalation_occurred": escalation_occurred,
            "direct_patch_attempted": candidate0_attempted,
            "direct_repair_first_used": fast_path_attempted,
            "hypothesis_stage_skipped_initially": candidate0_attempted,
            "initial_hypothesis_stage_skipped": candidate0_attempted,
            "direct_patch_target_file": next((d.get("target_file") for d in board.decision_log if d.get("event")=="target_selected"), ""),
            "target_file": next((d.get("target_file") for d in board.decision_log if d.get("event")=="target_selected"), ""),
            "target_selection_reason": next((d.get("target_selection_reason") for d in board.decision_log if d.get("event")=="target_selected"), ""),
            "escalated_after_direct_failure": escalation_occurred,
            "escalation_reason": next((d.get("reason") for d in board.decision_log if d.get("event")=="direct_repair_result"), ""),
            "exploration_block_triggered": any(a.verifier_outputs.get("exploration_block_triggered", False) for a in board.attempts),
            "prompt_tokens_first_attempt": next((a.verifier_outputs.get("prompt_tokens_first_attempt", 0) for a in board.attempts), 0),
            "tool_calls_first_attempt": next((a.verifier_outputs.get("tool_calls_first_attempt", 0) for a in board.attempts), 0),
            "workspace_prep_seconds": next((a.verifier_outputs.get("workspace_prep_seconds", 0.0) for a in board.attempts), 0.0),
            "model_execution_seconds": next((a.verifier_outputs.get("model_execution_seconds", 0.0) for a in board.attempts), 0.0),
            "verification_seconds": next((a.verifier_outputs.get("verification_seconds", 0.0) for a in board.attempts), 0.0),
            "candidate_total_seconds": next((a.verifier_outputs.get("candidate_total_seconds", 0.0) for a in board.attempts), 0.0),
            "session_context_reused": True,
        }
        store.write(board)
        summary = {
            "weak_search_cycles": board.cycle,
            "branches_created": len(board.branches),
            "branches_pruned": branches_pruned,
            "hypotheses_generated": len(board.hypotheses),
            "candidate_patches_generated": candidates_generated,
            "candidate_patches_verified": candidates_verified,
            "scope_expansions": 0,
            "no_progress_stop": board.final_result.get("stop_reason") == StopReason.NO_PROGRESS.value,
            "best_patch_score": best_score,
            "stop_reason": board.final_result.get("stop_reason"),
            "blocked_reason": board.final_result.get("blocked_reason", blocked_reason),
            "real_candidate_attempts": real_candidate_attempts,
            "noop_rejections": noop_rejections,
            "blocked_attempts": blocked_attempts,
            "verified_attempts": verified_attempts,
            "policy_profile": decision.profile.value,
            "strategy_selected": decision.strategy.value,
            "fast_path_attempted": fast_path_attempted,
            "candidate_0_attempted": candidate0_attempted,
            "escalation_occurred": escalation_occurred,
            "direct_patch_attempted": candidate0_attempted,
            "direct_repair_first_used": fast_path_attempted,
            "hypothesis_stage_skipped_initially": candidate0_attempted,
            "initial_hypothesis_stage_skipped": candidate0_attempted,
            "direct_patch_target_file": next((d.get("target_file") for d in board.decision_log if d.get("event")=="target_selected"), ""),
            "target_file": next((d.get("target_file") for d in board.decision_log if d.get("event")=="target_selected"), ""),
            "target_selection_reason": next((d.get("target_selection_reason") for d in board.decision_log if d.get("event")=="target_selected"), ""),
            "escalated_after_direct_failure": escalation_occurred,
            "escalation_reason": next((d.get("reason") for d in board.decision_log if d.get("event")=="direct_repair_result"), ""),
            "exploration_block_triggered": any(a.verifier_outputs.get("exploration_block_triggered", False) for a in board.attempts),
            "prompt_tokens_first_attempt": next((a.verifier_outputs.get("prompt_tokens_first_attempt", 0) for a in board.attempts), 0),
            "tool_calls_first_attempt": next((a.verifier_outputs.get("tool_calls_first_attempt", 0) for a in board.attempts), 0),
            "workspace_prep_seconds": next((a.verifier_outputs.get("workspace_prep_seconds", 0.0) for a in board.attempts), 0.0),
            "model_execution_seconds": next((a.verifier_outputs.get("model_execution_seconds", 0.0) for a in board.attempts), 0.0),
            "verification_seconds": next((a.verifier_outputs.get("verification_seconds", 0.0) for a in board.attempts), 0.0),
            "candidate_total_seconds": next((a.verifier_outputs.get("candidate_total_seconds", 0.0) for a in board.attempts), 0.0),
            "session_context_reused": True,
        }
        store.write_summary(summary)
        emit_runtime_event(self.repo, self.runner.event_callback, "weak_search_stopped", **summary)
        stop_reason = str(summary.get("stop_reason", ""))
        text = "Weak-search exhausted budget without a verified winner."
        if stop_reason == StopReason.SOLVED.value:
            text = f"Weak-search solved task with score {best_score:.3f}."
        elif stop_reason == StopReason.BLOCKED.value:
            text = f"Weak-search blocked: {summary.get('blocked_reason') or 'unknown'}."
        elif stop_reason == StopReason.NO_PROGRESS.value:
            text = "Weak-search stopped due to no progress."
        elif stop_reason == StopReason.TIMEOUT_IMMINENT.value:
            text = "Weak-search stopped due to imminent timeout."
        return {"response": {"role": "assistant", "content": [{"type": "text", "text": text}]}, "weak_search": summary}
