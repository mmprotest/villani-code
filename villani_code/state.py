from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any, Callable, Literal

from rich.console import Console

from villani_code.autonomous import VillaniModeConfig, VillaniModeController
from villani_code.autonomy import (
    FailureClassifier,
    VerificationEngine,
)
from villani_code.checkpoints import CheckpointManager
from villani_code.context_budget import ContextBudget
from villani_code.context_governance import ContextGovernanceManager
from villani_code.edits import ProposalStore
from villani_code.execution import ExecutionBudget, ExecutionResult
from villani_code.hooks import HookRunner
from villani_code.mcp import load_mcp_config
from villani_code.permissions import Decision, PermissionConfig, PermissionEngine
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.planning import TaskMode, classify_task_mode
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.llm_client import LLMClient
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.retrieval import Retriever
from villani_code.skills import discover_skills
from villani_code.streaming import StreamCoalescer, assemble_anthropic_stream
from villani_code.tools import tool_specs
from villani_code.transcripts import save_transcript
from villani_code.state_execution import (
    collect_runner_failures,
    collect_validation_artifacts,
    summarize_changes,
)
from villani_code.utils import (
    is_effectively_empty_content,
    merge_extra_json,
    normalize_content_blocks,
)


class Runner:
    def __init__(
        self,
        client: LLMClient,
        repo: Path,
        model: str,
        max_tokens: int = 4096,
        stream: bool = True,
        print_stream: bool = True,
        thinking: Any = None,
        unsafe: bool = False,
        verbose: bool = False,
        extra_json: str | None = None,
        redact: bool = False,
        bypass_permissions: bool = False,
        auto_accept_edits: bool = False,
        plan_mode: Literal["off", "auto", "strict"] = "auto",
        max_repair_attempts: int = 2,
        approval_callback: Callable[[str, dict[str, Any]], bool] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        small_model: bool = False,
        villani_mode: bool = False,
        villani_objective: str | None = None,
        benchmark_config: BenchmarkRuntimeConfig | None = None,
        runtime: str = "classic",
    ):
        self.client = client
        self.repo = repo
        ensure_runtime_dependencies_not_shadowed(self.repo)
        self.model = model
        self.max_tokens = max_tokens
        self.stream = stream
        self.print_stream = print_stream
        self.thinking = thinking
        self.unsafe = unsafe
        self.verbose = verbose
        self.extra_json = extra_json
        self.redact = redact
        self.bypass_permissions = bypass_permissions
        self.auto_accept_edits = auto_accept_edits
        self.plan_mode = plan_mode
        self.max_repair_attempts = max_repair_attempts
        self.approval_callback = approval_callback or (lambda _n, _i: True)
        self.event_callback = event_callback or (lambda _event: None)
        self.small_model = small_model
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective
        self.villani_config = VillaniModeConfig(
            enabled=villani_mode, steering_objective=villani_objective
        )
        self.benchmark_config = benchmark_config or BenchmarkRuntimeConfig()
        self.runtime = runtime
        self._benchmark_noop_completion_attempts = 0
        self.console = Console()
        self.permissions = PermissionEngine(
            PermissionConfig.from_strings(
                deny=["Read(.env)", "Read(secrets/**)", "Bash(curl *)", "Bash(wget *)"],
                ask=["Write(*)", "Patch(*)"],
                allow=[
                    "Read(*)",
                    "Ls(*)",
                    "Grep(*)",
                    "Search(*)",
                    "Glob(*)",
                    "BashSafe(*)",
                    "GitStatus(*)",
                    "GitDiff(*)",
                    "GitLog(*)",
                    "GitBranch(*)",
                    "GitCheckout(*)",
                    "GitCommit(*)",
                ],
            ),
            repo=self.repo,
        )
        self.hooks = HookRunner(hooks={})
        self.checkpoints = CheckpointManager(self.repo)
        self.skills = discover_skills(self.repo)
        self.mcp = load_mcp_config(self.repo)
        self.proposals = ProposalStore(self.repo / ".villani_code" / "edits")
        self.capture_next_diff_proposal = False
        self._coalescer = StreamCoalescer()
        self._live_stream_buffer = ""
        self._live_stream_started = False
        self._no_progress_cycles = 0
        self._recovery_count = 0
        self._last_failed_tool_sig = ""
        self._repo_map = ""
        self._retriever: Retriever | None = None
        self._context_budget = (
            ContextBudget(max_chars=35000, keep_last_turns=4)
            if self.small_model
            else None
        )
        self._files_read: set[str] = set()
        self._pending_verification = ""
        self._intended_targets: set[str] = set()
        self._before_contents: dict[str, str] = {}
        self._current_verification_targets: set[str] = set()
        self._current_verification_before_contents: dict[str, str] = {}
        self._verification_baseline_changed: set[str] = set()
        self._scope_expansion_used = False
        self._task_mode: TaskMode = TaskMode.GENERAL
        self._task_contract: dict[str, Any] = {}
        self._last_verification_fingerprint = ""
        self._repeated_stale_verification_count = 0
        self._last_verification_intentional: set[str] = set()
        self._last_verification_artifact_count = 0
        self._failure_classifier = FailureClassifier()
        self._context_governance = ContextGovernanceManager(self.repo)
        self._verification_engine = VerificationEngine(self.repo)
        if self.small_model:
            self._init_small_model_support()

    def run_villani_mode(self) -> dict[str, Any]:
        ensure_runtime_dependencies_not_shadowed(self.repo)
        controller = VillaniModeController(
            self,
            self.repo,
            steering_objective=self.villani_objective,
            event_callback=self.event_callback,
        )
        summary = controller.run()
        text = VillaniModeController.format_summary(summary)
        response = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        return {"response": response, "summary": summary}

    def run(
        self,
        instruction: str,
        messages: list[dict[str, Any]] | None = None,
        execution_budget: ExecutionBudget | None = None,
    ) -> dict[str, Any]:
        messages = messages or build_initial_messages(self.repo, instruction)
        self._ensure_project_memory_and_plan(instruction)
        self._task_mode = classify_task_mode(instruction)
        tools = tool_specs()
        transcript: dict[str, Any] = {
            "requests": [],
            "responses": [],
            "tool_invocations": [],
            "tool_results": [],
            "streamed_events_count": 0,
        }
        if self.benchmark_config.enabled and self.runtime == "weak-search":
            from villani_code.runtime.controller import WeakSearchController

            timeout_seconds = 300.0
            if execution_budget:
                timeout_seconds = float(execution_budget.max_seconds)
            return WeakSearchController(self, instruction, timeout_seconds=timeout_seconds).run()

        if self.benchmark_config.enabled:
            self.event_callback({
                "type": "benchmark_mode_enabled",
                "task_id": self.benchmark_config.task_id,
                "allowlist_paths": self.benchmark_config.allowlist_paths,
                "expected_files": self.benchmark_config.expected_files,
            })
        self._save_session_snapshot(messages)
        empty_turn_retries = 0
        start = time.monotonic()
        turns_used = 0
        tool_calls_used = 0
        consecutive_no_edit_turns = 0
        consecutive_recon_turns = 0
        baseline_changed = set(self._git_changed_files())
        self._verification_baseline_changed = set(baseline_changed)
        self._intended_targets: set[str] = set()
        self._before_contents: dict[str, str] = {}
        self._current_verification_targets: set[str] = set()
        self._current_verification_before_contents: dict[str, str] = {}
        self._last_verification_fingerprint = ""
        self._repeated_stale_verification_count = 0
        self._last_verification_intentional = set()
        self._last_verification_artifact_count = 0
        self._scope_expansion_used = False

        source_targets = list(getattr(getattr(self, "_execution_plan", None), "relevant_files", []))
        if self.benchmark_config.enabled:
            source_targets.extend(self.benchmark_config.expected_files)
            source_targets.extend(self.benchmark_config.allowlist_paths)
        seen_targets: set[str] = set()
        deduped_targets: list[str] = []
        for target in source_targets:
            norm = str(target).replace("\\", "/").lstrip("./")
            if not norm or norm in seen_targets:
                continue
            seen_targets.add(norm)
            deduped_targets.append(norm)
        preferred_targets = [p for p in deduped_targets if not p.startswith("tests/")] + [p for p in deduped_targets if p.startswith("tests/")]
        no_go_paths = [".git/", ".villani_code/", "__pycache__/"]
        if self.benchmark_config.enabled:
            no_go_paths.extend(self.benchmark_config.forbidden_paths)
        success_predicates = {
            TaskMode.FIX_FAILING_TEST: "patch the failing implementation or directly relevant test target and improve failing verification",
            TaskMode.FIX_LINT_OR_TYPE: "resolve the lint/type issue with minimal file scope",
            TaskMode.NARROW_REFACTOR: "perform a bounded refactor without widening scope",
            TaskMode.DOCS_UPDATE_SAFE: "docs-only update with no code edits",
            TaskMode.INSPECT_AND_PLAN: "inspect repo and stop without code edits unless explicit evidence makes a tiny patch unavoidable",
            TaskMode.GENERAL: "make one bounded, verifiable repo improvement",
        }
        self._task_contract = {
            "task_mode": self._task_mode.value,
            "success_predicate": success_predicates.get(self._task_mode, success_predicates[TaskMode.GENERAL]),
            "preferred_targets": preferred_targets[:6],
            "no_go_paths": sorted(set(no_go_paths)),
        }
        system = build_system_blocks(
            self.repo,
            repo_map=self._repo_map if self.small_model else "",
            villani_mode=self.villani_mode,
            benchmark_config=self.benchmark_config,
            task_mode=self._task_mode,
        )
        if self.small_model or self.villani_mode or self.benchmark_config.enabled:
            preferred_text = ", ".join(self._task_contract["preferred_targets"][:2]) or "none yet"
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Task contract ({self._task_contract['task_mode']}): name likely target file first (prefer {preferred_text}); "
                                f"keep scope tight; verify against: {self._task_contract['success_predicate']}; avoid speculative multi-file edits."
                                " Stop if verification repeats without new evidence."
                            ),
                        }
                    ],
                }
            )
        previous_attributed = set()

        def _attributed_changed_files() -> list[str]:
            current = set(self._git_changed_files())
            return sorted(current - baseline_changed)

        def _change_summary() -> tuple[list[str], list[str], list[str]]:
            summary = summarize_changes(_attributed_changed_files())
            return summary.intentional, summary.incidental, summary.all_changes

        def _has_meaningful_benchmark_edit() -> bool:
            if not self.benchmark_config.enabled:
                return True
            intentional_changes, _incidental, _all = _change_summary()
            if not intentional_changes:
                return False
            meaningful = [
                path
                for path in intentional_changes
                if self.benchmark_config.in_allowlist(path)
                and self.benchmark_config.is_expected_or_support(path)
            ]
            return bool(meaningful)

        def _finish_bounded(
            response: dict[str, Any], reason: str, completed: bool
        ) -> dict[str, Any]:
            elapsed = time.monotonic() - start
            intentional_changes, incidental_changes, all_changes = _change_summary()
            final_text = "\n".join(
                block.get("text", "")
                for block in response.get("content", [])
                if block.get("type") == "text"
            )
            execution = ExecutionResult(
                final_text=final_text,
                turns_used=turns_used,
                tool_calls_used=tool_calls_used,
                elapsed_seconds=elapsed,
                files_changed=all_changes,
                intentional_changes=intentional_changes,
                incidental_changes=incidental_changes,
                all_changes=all_changes,
                intended_targets=sorted(self._intended_targets),
                before_contents=dict(self._before_contents),
                validation_artifacts=collect_validation_artifacts(transcript),
                inspection_summary="",
                runner_failures=collect_runner_failures(transcript),
                terminated_reason=reason,
                completed=completed,
            )
            transcript["execution"] = execution.to_dict()
            transcript["final_assistant_content"] = response.get("content", [])
            transcript_path = save_transcript(self.repo, transcript, redact=self.redact)
            post = self._run_post_execution_validation(_change_summary()[2])
            if post:
                response.setdefault("content", []).append({"type": "text", "text": post})
            self._save_session_snapshot(messages)
            return {
                "response": response,
                "messages": messages,
                "transcript_path": str(transcript_path),
                "transcript": transcript,
                "execution": execution.to_dict(),
            }

        def _budget_reason(
            completed: bool = False, model_idle: bool = False
        ) -> str | None:
            if execution_budget is None:
                return None
            elapsed = time.monotonic() - start
            if elapsed > execution_budget.max_seconds:
                return "max_seconds"
            if tool_calls_used >= execution_budget.max_tool_calls:
                return "max_tool_calls"
            if turns_used >= execution_budget.max_turns:
                return "max_turns"
            if (
                consecutive_recon_turns
                >= execution_budget.max_reconsecutive_recon_turns
            ):
                return "recon_loop"
            if consecutive_no_edit_turns >= execution_budget.max_no_edit_turns:
                return "no_edits"
            if model_idle:
                return "model_idle"
            if completed:
                return "completed"
            return None

        while True:
            self._live_stream_buffer = ""
            self._live_stream_started = False
            self._coalescer = StreamCoalescer()
            turn_messages = self._prepare_messages_for_model(messages)
            payload = {
                "model": self.model,
                "messages": turn_messages,
                "system": system,
                "tools": tools,
                "max_tokens": self.max_tokens,
                "stream": self.stream,
            }
            if self.thinking is not None:
                payload["thinking"] = self.thinking
            payload = merge_extra_json(payload, self.extra_json)
            transcript["requests"].append(payload)
            self.event_callback({"type": "model_request_started", "model": self.model})

            raw = self.client.create_message(payload, stream=self.stream)
            if self.stream:
                events = []
                for event in raw:
                    events.append(event)
                    self._render_stream_event(event)
                transcript["streamed_events_count"] += len(events)
                response = assemble_anthropic_stream(events)
            else:
                response = raw

            response["content"] = normalize_content_blocks(response.get("content"))
            transcript["responses"].append(response)
            messages.append(
                {"role": "assistant", "content": response.get("content", [])}
            )
            turns_used += 1

            tool_uses = [
                b for b in response.get("content", []) if b.get("type") == "tool_use"
            ]
            empty = is_effectively_empty_content(response.get("content", []))
            if not tool_uses and empty and empty_turn_retries < 2:
                empty_turn_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Continue. You ended your previous turn with no output. Resume the task from where you left off and either call the next tool or provide the next part of the answer.",
                            }
                        ],
                    }
                )
                reason = _budget_reason()
                if reason:
                    return _finish_bounded(response, reason, reason == "completed")
                continue
            if tool_uses or not empty:
                empty_turn_retries = 0
            if not tool_uses:
                if empty:
                    if self.benchmark_config.enabled and not _has_meaningful_benchmark_edit():
                        self._benchmark_noop_completion_attempts += 1
                        self.event_callback({"type": "benchmark_noop_completion_blocked", "task_id": self.benchmark_config.task_id, "attempt": self._benchmark_noop_completion_attempts})
                        if self._benchmark_noop_completion_attempts >= 2:
                            return _finish_bounded(response, "benchmark_incomplete_no_patch", False)
                        reminder = "Benchmark mode requires an actual in-scope patch. Edit only expected/allowed support files and continue."
                        self.event_callback({"type": "benchmark_scope_reminder_injected", "task_id": self.benchmark_config.task_id, "reason": "no_meaningful_edit"})
                        messages.append({"role": "user", "content": [{"type": "text", "text": reminder}]})
                        continue
                    reason = _budget_reason(completed=True)
                    if reason:
                        return _finish_bounded(response, reason, reason == "completed")
                    transcript["final_assistant_content"] = response.get("content", [])
                    transcript_path = save_transcript(
                        self.repo, transcript, redact=self.redact
                    )
                    post = self._run_post_execution_validation(_change_summary()[2])
                    if post:
                        response.setdefault("content", []).append({"type": "text", "text": post})
                    self._save_session_snapshot(messages)
                    return {
                        "response": response,
                        "messages": messages,
                        "transcript_path": str(transcript_path),
                        "transcript": transcript,
                    }
                proposal = self._capture_edit_proposal(response)
                if proposal:
                    self.event_callback(
                        {
                            "type": "edit_proposed",
                            "proposal_id": proposal.id,
                            "summary": proposal.summary,
                            "files": proposal.files_touched,
                        }
                    )
                if self._is_no_progress_response(response):
                    self._no_progress_cycles += 1
                    if execution_budget is not None:
                        reason = _budget_reason(model_idle=True)
                        if reason:
                            return _finish_bounded(
                                response, reason, reason == "completed"
                            )
                    constrained = self.small_model or self.villani_mode or self.benchmark_config.enabled
                    if constrained and self._recovery_count == 0:
                        messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "RECOVERY MODE: State the single target file, the exact verification goal, and make exactly one next tool call."}],
                            }
                        )
                        self._recovery_count = 1
                        self._no_progress_cycles = 0
                        continue
                    if constrained and self._recovery_count == 1:
                        messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "RECOVERY MODE: Do not edit yet. In <=5 lines explain the blocker, inspect exactly one relevant file/diff, then either patch the locked target or finish."}],
                            }
                        )
                        self._recovery_count = 2
                        self._no_progress_cycles = 0
                        continue
                    if constrained and self._recovery_count >= 2:
                        blocked_reason = "repeated no-progress recovery with no new verification evidence"
                        response = {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"Stopping due to constrained-run blocker: {blocked_reason}. "
                                        f"Locked targets: {sorted(self._intended_targets)}. "
                                        f"Scope expansion consumed: {self._scope_expansion_used}. "
                                        "Missing evidence: a new bounded patch or new verification signal. "
                                        f"Success predicate: {self._task_contract.get('success_predicate', 'make one bounded, verifiable repo improvement')}."
                                    ),
                                }
                            ],
                        }
                        transcript["responses"].append(response)
                    elif not constrained and self._recovery_count == 0:
                        messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "RECOVERY MODE: State the single target file, the exact verification goal, and make exactly one next tool call."}],
                            }
                        )
                        self._recovery_count = 1
                        self._no_progress_cycles = 0
                        continue
                    elif not constrained and self._recovery_count == 1:
                        messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "RECOVERY MODE: Do not edit yet. In <=5 lines explain the blocker, inspect exactly one relevant file/diff, then either patch the locked target or finish."}],
                            }
                        )
                        self._recovery_count = 2
                        self._no_progress_cycles = 0
                        continue
                    elif not constrained and self._recovery_count >= 2:
                        response = {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "I’m still blocked after two recovery attempts. Which target scope or missing verification evidence should I relax first?",
                                }
                            ],
                        }
                        transcript["responses"].append(response)
                else:
                    self._no_progress_cycles = 0
                    self._recovery_count = 0
                if self.benchmark_config.enabled and not _has_meaningful_benchmark_edit():
                    self._benchmark_noop_completion_attempts += 1
                    self.event_callback({"type": "benchmark_noop_completion_blocked", "task_id": self.benchmark_config.task_id, "attempt": self._benchmark_noop_completion_attempts})
                    if self._benchmark_noop_completion_attempts >= 2:
                        return _finish_bounded(response, "benchmark_incomplete_no_patch", False)
                    reminder = "Benchmark mode requires a real patch in task scope before completion."
                    self.event_callback({"type": "benchmark_scope_reminder_injected", "task_id": self.benchmark_config.task_id, "reason": "no_meaningful_edit"})
                    messages.append({"role": "user", "content": [{"type": "text", "text": reminder}]})
                    continue
                reason = _budget_reason(completed=True)
                if reason:
                    return _finish_bounded(response, reason, reason == "completed")
                transcript["final_assistant_content"] = response.get("content", [])
                transcript_path = save_transcript(
                    self.repo, transcript, redact=self.redact
                )
                self._save_session_snapshot(messages)
                return {
                    "response": response,
                    "messages": messages,
                    "transcript_path": str(transcript_path),
                    "transcript": transcript,
                }

            tool_results: list[dict[str, Any]] = []
            for block in tool_uses:
                tool_name = block.get("name", "")
                tool_input = dict(block.get("input", {}))
                tool_use_id = str(block.get("id"))
                self.event_callback(
                    {
                        "type": "tool_use",
                        "name": tool_name,
                        "input": tool_input,
                        "tool_use_id": tool_use_id,
                    }
                )

                result = self._execute_tool_with_policy(
                    tool_name, tool_input, tool_use_id, len(messages)
                )
                tool_calls_used += 1
                if self.small_model:
                    result = self._truncate_tool_result(tool_name, result)
                    if tool_name == "Read" and not result.get("is_error"):
                        self._files_read.add(str(tool_input.get("file_path", "")))
                    if tool_name in {"Write", "Patch"} and not result.get("is_error"):
                        self._pending_verification = self._run_verification()

                if tool_name in {"Write", "Patch", "Bash"}:
                    self._pending_verification = self._run_verification(
                        trigger=f"{tool_name} execution"
                    )

                if result.get("is_error"):
                    failure = self._failure_classifier.classify(
                        f"{tool_name} failed", str(result.get("content", ""))
                    )
                    self.event_callback(
                        {
                            "type": "failure_classified",
                            "category": failure.category.value,
                            "summary": failure.cause_summary,
                            "next_strategy": failure.suggested_strategy,
                            "occurrence": failure.occurrence_count,
                        }
                    )

                self.hooks.run_event(
                    "PostToolUse",
                    {
                        "event": "PostToolUse",
                        "tool": tool_name,
                        "input": tool_input,
                        "result": result,
                    },
                )
                self.event_callback(
                    {
                        "type": "tool_finished",
                        "name": tool_name,
                        "input": tool_input,
                        "tool_use_id": tool_use_id,
                        "is_error": result["is_error"],
                    }
                )
                transcript["tool_invocations"].append(
                    {"name": tool_name, "input": tool_input, "id": tool_use_id}
                )
                transcript["tool_results"].append(result)
                self.event_callback(
                    {
                        "type": "tool_result",
                        "name": tool_name,
                        "input": tool_input,
                        "tool_use_id": tool_use_id,
                        "is_error": result["is_error"],
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result["content"],
                        "is_error": result["is_error"],
                    }
                )

                reason = _budget_reason()
                if reason:
                    return _finish_bounded(response, reason, reason == "completed")

            if tool_results and any(
                not r.get("is_error")
                for r in transcript["tool_results"][-len(tool_uses) :]
            ):
                self._no_progress_cycles = 0
                self._recovery_count = 0
                self._last_failed_tool_sig = ""
            else:
                sig = "|".join(f"{b.get('name')}:{b.get('input')}" for b in tool_uses)
                if sig and sig == self._last_failed_tool_sig:
                    self._no_progress_cycles += 1
                self._last_failed_tool_sig = sig

            attributed = set(_attributed_changed_files())
            edited_this_turn = attributed != previous_attributed
            previous_attributed = attributed
            if edited_this_turn:
                consecutive_no_edit_turns = 0
                if self.benchmark_config.enabled and _has_meaningful_benchmark_edit():
                    self._benchmark_noop_completion_attempts = 0
            else:
                consecutive_no_edit_turns += 1

            mutating_tools = any(
                self._is_mutating_tool_call(b.get("name", ""), dict(b.get("input", {})))
                for b in tool_uses
            )
            recon_turn = bool(tool_uses) and not mutating_tools and not edited_this_turn
            if recon_turn:
                consecutive_recon_turns += 1
            else:
                consecutive_recon_turns = 0

            reason = _budget_reason()
            if reason:
                return _finish_bounded(response, reason, reason == "completed")
            next_user_content = copy.deepcopy(tool_results)
            if self._pending_verification and next_user_content:
                existing = str(next_user_content[-1].get("content", ""))
                next_user_content[-1]["content"] = (
                    f"{existing}\n\n{self._pending_verification}"
                    if existing
                    else self._pending_verification
                )
                self._pending_verification = ""
            messages.append({"role": "user", "content": next_user_content})

            reason = _budget_reason()
            if reason:
                return _finish_bounded(response, reason, reason == "completed")

    def _is_mutating_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> bool:
        if tool_name in {"Write", "Patch", "GitCheckout", "GitCommit"}:
            return True
        if tool_name == "Bash":
            command = str(tool_input.get("command", "")).strip().lower()
            readonly_prefixes = (
                "git status",
                "git diff",
                "git log",
                "ls",
                "cat",
                "rg ",
                "grep ",
                "find ",
                "pwd",
            )
            return not command.startswith(readonly_prefixes)
        return False

    def _execute_tool_with_policy(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
        message_count: int,
    ) -> dict[str, Any]:
        from villani_code import state_tooling

        return state_tooling.execute_tool_with_policy(
            self,
            tool_name,
            tool_input,
            tool_use_id,
            message_count,
        )


    def _prepare_messages_for_model(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        from villani_code import state_runtime

        return state_runtime.prepare_messages_for_model(self, messages)

    def _inject_retrieval_briefing(self, messages: list[dict[str, Any]]) -> None:
        from villani_code import state_runtime

        state_runtime.inject_retrieval_briefing(self, messages)

    def _init_small_model_support(self) -> None:
        from villani_code import state_runtime

        state_runtime.init_small_model_support(self)

    def _small_model_tool_guard(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> str | None:
        from villani_code import state_runtime

        return state_runtime.small_model_tool_guard(self, tool_name, tool_input)

    def _tighten_tool_input(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        from villani_code import state_runtime

        state_runtime.tighten_tool_input(tool_name, tool_input)

    def _truncate_tool_result(
        self, tool_name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        from villani_code import state_runtime

        return state_runtime.truncate_tool_result(tool_name, result)

    def _run_verification(self, trigger: str = "edit") -> str:
        from villani_code import state_runtime

        return state_runtime.run_verification(self, trigger)

    def _git_changed_files(self) -> list[str]:
        from villani_code import state_runtime

        return state_runtime.git_changed_files(self.repo)

    def _emit_policy_event(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        decision: Decision,
        reason: str,
    ) -> None:
        from villani_code import state_runtime

        state_runtime.emit_policy_event(self, tool_name, tool_input, decision, reason)

    def _capture_edit_proposal(self, response: dict[str, Any]):
        from villani_code import state_runtime

        return state_runtime.capture_edit_proposal(self, response)

    def _is_no_progress_response(self, response: dict[str, Any]) -> bool:
        from villani_code import state_runtime

        return state_runtime.is_no_progress_response(response)

    def _save_session_snapshot(self, messages: list[dict[str, Any]]) -> None:
        from villani_code import state_runtime

        state_runtime.save_session_snapshot(self, messages)

    def _render_stream_event(self, event: dict[str, Any]) -> None:
        from villani_code import state_runtime

        state_runtime.render_stream_event(self, event)

    def _ensure_project_memory_and_plan(self, instruction: str) -> None:
        from villani_code import state_runtime

        state_runtime.ensure_project_memory_and_plan(self, instruction)

    def _run_post_execution_validation(self, changed_files: list[str]) -> str:
        from villani_code import state_runtime

        return state_runtime.run_post_execution_validation(self, changed_files)
