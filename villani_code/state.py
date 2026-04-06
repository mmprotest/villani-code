from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

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
from villani_code.plan_session import PlanAnswer, PlanOption, PlanQuestion, PlanSessionResult
from villani_code.prompting import (
    build_approved_plan_context,
    build_initial_messages,
    build_planning_instruction,
    build_system_blocks,
)
from villani_code.planning import TaskMode, classify_task_mode
from villani_code.benchmark.runtime_config import BenchmarkRuntimeConfig
from villani_code.llm_client import LLMClient
from villani_code.runtime_safety import ensure_runtime_dependencies_not_shadowed
from villani_code.retrieval import Retriever
from villani_code.skills import discover_skills
from villani_code.streaming import StreamCoalescer, assemble_anthropic_stream
from villani_code.tools import tool_specs
from villani_code.transcripts import save_transcript
from villani_code.context_projection import build_model_context_packet, render_model_context_packet
from villani_code.event_recorder import RuntimeEventRecorder
from villani_code.debug_mode import DebugConfig, DebugMode
from villani_code.debug_recorder import DebugRecorder
from villani_code.mission_state import MissionState, create_mission_state, get_mission_dir, save_mission_state
from villani_code.summarizer import summarize_mission_state
from villani_code.state_execution import (
    collect_runner_failures,
    collect_validation_artifacts,
    summarize_changes,
)
from villani_code.utils import (
    is_path_within,
    is_effectively_empty_content,
    merge_extra_json,
    normalize_content_blocks,
)


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered




def _read_text_excerpt(path: Path, limit: int = 1400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[:limit]


def _select_planning_evidence_files(repo: Path, instruction: str, repo_map: dict[str, Any]) -> list[str]:
    lowered = instruction.lower()
    candidates: list[str] = []
    candidates.extend(str(v) for v in repo_map.get("likely_entrypoints", []))
    candidates.extend(str(v) for v in repo_map.get("manifests", []))
    candidates.extend(str(v) for v in repo_map.get("config_files", []))

    if any(token in lowered for token in ("tui", "slash", "plan", "execute", "ui")):
        candidates.extend([
            "villani_code/tui/app.py",
            "villani_code/tui/controller.py",
            "villani_code/tui/components/command_palette.py",
            "villani_code/tui/widgets/plan_question.py",
        ])
    if any(token in lowered for token in ("state", "runner", "workflow", "plan")):
        candidates.extend([
            "villani_code/state.py",
            "villani_code/state_tooling.py",
            "villani_code/prompting.py",
        ])
    if any(token in lowered for token in ("test", "quality", "improve", "review", "repo")):
        candidates.extend([
            "tests/test_plan_workflow.py",
            "tests/test_ui_slash_commands.py",
        ])

    existing: list[str] = []
    for raw in _dedupe_preserve(candidates):
        target = (repo / raw).resolve()
        if target.exists() and target.is_file() and is_path_within(repo, target):
            existing.append(raw)
        if len(existing) >= 12:
            break
    return existing


def _collect_planning_evidence(repo: Path, instruction: str, repo_map: dict[str, Any]) -> list[dict[str, str]]:
    files = _select_planning_evidence_files(repo, instruction, repo_map)
    evidence: list[dict[str, str]] = []
    for rel in files:
        excerpt = _read_text_excerpt(repo / rel)
        if not excerpt.strip():
            continue
        evidence.append({"path": rel, "excerpt": excerpt})
    return evidence




def _normalize_plan_text_item(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        preferred_keys = [
            "action",
            "step",
            "path",
            "target",
            "focus",
            "improvement_focus",
            "risk",
            "mitigation",
            "validation",
            "check",
            "reason",
            "summary",
            "label",
        ]
        values: list[str] = []
        for key in preferred_keys:
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                values.append(text)
        return " — ".join(values)
    if isinstance(item, (list, tuple)):
        return "; ".join(str(value).strip() for value in item if str(value).strip())
    return str(item).strip()


def _normalize_plan_text_list(items: Any, limit: int = 16) -> list[str]:
    if not isinstance(items, list):
        return []
    normalized = [_normalize_plan_text_item(item) for item in items]
    return _dedupe_preserve([item for item in normalized if item])[:limit]

def _normalize_open_questions(raw_questions: Any) -> list[PlanQuestion]:
    open_questions: list[PlanQuestion] = []
    if not isinstance(raw_questions, list):
        return open_questions
    for idx, item in enumerate(raw_questions[:3], start=1):
        if not isinstance(item, dict):
            continue
        options_raw = item.get("options", [])
        if not isinstance(options_raw, list):
            continue
        options: list[PlanOption] = []
        for opt in options_raw[:4]:
            if not isinstance(opt, dict):
                continue
            options.append(
                PlanOption(
                    id=str(opt.get("id", "")).strip() or f"q{idx}_opt{len(options)+1}",
                    label=str(opt.get("label", "")).strip() or f"Option {len(options)+1}",
                    description=str(opt.get("description", "")).strip(),
                    is_other=bool(opt.get("is_other", False)),
                )
            )
        if len(options) != 4:
            continue
        try:
            open_questions.append(
                PlanQuestion(
                    id=str(item.get("id", "")).strip() or f"q{idx}",
                    question=str(item.get("question", "")).strip(),
                    rationale=str(item.get("rationale", "")).strip(),
                    options=options,
                )
            )
        except ValueError:
            continue
    return open_questions


def _normalize_validation_items(artifact: dict[str, Any]) -> list[str]:
    return _normalize_plan_text_list(
        artifact.get("validation_approach", artifact.get("validation", [])),
        limit=16,
    )


def _is_coherent_plan_artifact(artifact: dict[str, Any], instruction: str) -> bool:
    task_summary = str(artifact.get("task_summary", "")).strip() or instruction.strip()
    steps = _normalize_plan_text_list(artifact.get("recommended_steps", []), limit=24)
    open_questions = _normalize_open_questions(artifact.get("open_questions", []))
    validation_items = _normalize_validation_items(artifact)
    return bool(task_summary and (steps or open_questions or validation_items))


def _parse_strict_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if not match:
            return None
        text = match.group(1).strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    required = ("task_summary", "candidate_files", "recommended_steps")
    if not all(key in parsed for key in required):
        return None
    return parsed


def _extract_strict_json_plan_artifact(run_result: dict[str, Any]) -> dict[str, Any] | None:
    response = run_result.get("response", {})
    if not isinstance(response, dict):
        return None
    content = response.get("content", [])
    if not isinstance(content, list):
        return None
    for block in reversed(content):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        parsed = _parse_strict_json_object(str(block.get("text", "")))
        if parsed is not None:
            return parsed
    return None


def _extract_assistant_plan_text(run_result: dict[str, Any]) -> str:
    response = run_result.get("response", {})
    if not isinstance(response, dict):
        return ""
    content = response.get("content", [])
    if not isinstance(content, list):
        return ""
    blocks: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text", "")).strip()
        if text:
            blocks.append(text)
    return "\n".join(blocks).strip()


def format_plan_text_to_artifact(instruction: str, plan_text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in plan_text.splitlines()]
    heading_aliases = {
        "objective": "objective",
        "files": "files",
        "candidate files": "files",
        "steps": "steps",
        "recommended steps": "steps",
        "implementation steps": "steps",
        "validation": "validation",
        "open questions": "open_questions",
        "questions": "open_questions",
        "assumptions": "assumptions",
        "risks": "assumptions",
    }
    section = "general"
    sections: dict[str, list[str]] = {name: [] for name in ("objective", "files", "steps", "validation", "open_questions", "assumptions", "general")}
    general_bullets: list[str] = []
    bullet_pattern = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
    file_pattern = re.compile(r"(?:[\w./-]+/)*[\w.-]+\.(?:py|md|txt|json|ya?ml|toml|ini|cfg|sh|js|ts|tsx|jsx|css|html|sql|rst)")

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        heading_match = re.match(r"^(Objective|Files|Candidate Files|Steps|Recommended Steps|Implementation Steps|Validation|Open Questions|Questions|Assumptions|Risks)\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if heading_match:
            section = heading_aliases.get(heading_match.group(1).strip().lower(), "general")
            remainder = heading_match.group(2).strip()
            if remainder:
                sections[section].append(remainder)
            continue
        bullet_match = bullet_pattern.match(line)
        entry = bullet_match.group(1).strip() if bullet_match else line
        sections[section].append(entry)
        if bullet_match and section == "general":
            general_bullets.append(entry)

    objective = " ".join(sections["objective"]).strip() or instruction.strip()
    candidate_files = _dedupe_preserve(
        [match.group(0) for text in (sections["files"] + sections["general"] + sections["steps"]) for match in file_pattern.finditer(text)]
    )[:16]
    recommended_steps = _dedupe_preserve([step for step in sections["steps"] if step])[:24]
    if not recommended_steps:
        recommended_steps = _dedupe_preserve(general_bullets)[:24]

    validation_items = [item for item in sections["validation"] if item]
    if not validation_items:
        validation_markers = ("pytest", "unittest", "tox", "nox", "mypy", "ruff", "test", "validate", "verification")
        derived_validation: list[str] = []
        for item in [*recommended_steps, *sections["general"]]:
            lowered = item.lower()
            if any(marker in lowered for marker in validation_markers):
                derived_validation.append(item)
        validation_items = _dedupe_preserve(derived_validation)[:16]
    open_question_lines = [item for item in sections["open_questions"] if item]
    open_questions = []
    for idx, question_text in enumerate(open_question_lines[:3], start=1):
        if "?" not in question_text:
            continue
        open_questions.append(
            {
                "id": f"q{idx}",
                "question": question_text,
                "rationale": "Clarification required before execution.",
                "options": [
                    {"id": f"q{idx}_opt1", "label": "Option 1", "description": "", "is_other": False},
                    {"id": f"q{idx}_opt2", "label": "Option 2", "description": "", "is_other": False},
                    {"id": f"q{idx}_opt3", "label": "Option 3", "description": "", "is_other": False},
                    {"id": f"q{idx}_opt4", "label": "Other", "description": "", "is_other": True},
                ],
            }
        )

    assumptions = _dedupe_preserve([*sections["assumptions"], *validation_items, *sections["general"]])[:24]
    return {
        "task_summary": objective,
        "candidate_files": candidate_files,
        "assumptions": assumptions,
        "recommended_steps": recommended_steps,
        "validation_approach": validation_items,
        "open_questions": open_questions,
    }


def _build_plan_result_from_artifact(
    instruction: str,
    artifact: dict[str, Any],
    resolved_answers: list[PlanAnswer],
    evidence_paths: list[str],
) -> PlanSessionResult | None:
    task_summary = str(artifact.get("task_summary", "")).strip() or instruction.strip()
    candidate_files = _normalize_plan_text_list(artifact.get("candidate_files", []), limit=16)
    assumptions = _normalize_plan_text_list(artifact.get("assumptions", []), limit=24)
    recommended_steps = _normalize_plan_text_list(artifact.get("recommended_steps", []), limit=24)
    validation_items = _normalize_validation_items(artifact)
    if not _is_coherent_plan_artifact(artifact, instruction):
        return None

    open_questions = _normalize_open_questions(artifact.get("open_questions", []))
    assumptions.extend(validation_items)
    assumptions.extend(f"Evidence inspected: {path}" for path in evidence_paths[:8])
    assumptions.extend(_format_answer(answer) for answer in resolved_answers)
    assumptions = _dedupe_preserve(assumptions)

    has_required_execution_detail = bool(recommended_steps and validation_items)
    ready = has_required_execution_detail and not open_questions
    brief = "\n".join([task_summary, *recommended_steps])
    return PlanSessionResult(
        instruction=instruction,
        task_summary=task_summary,
        candidate_files=candidate_files,
        assumptions=assumptions,
        recommended_steps=recommended_steps,
        open_questions=open_questions,
        resolved_answers=resolved_answers,
        ready_to_execute=ready,
        execution_brief=brief,
        risk_level=str(artifact.get("risk_level", "medium")),
        confidence_score=float(artifact.get("confidence_score", 0.65) or 0.65),
    )


def _format_answer(plan_answer: PlanAnswer) -> str:
    value = plan_answer.other_text.strip() if plan_answer.other_text.strip() else plan_answer.selected_option_id
    return f"{plan_answer.question_id}: {value}"



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
        auto_approve: bool = False,
        plan_mode: Literal["off", "auto", "strict"] = "auto",
        max_repair_attempts: int = 2,
        approval_callback: Callable[[str, dict[str, Any]], bool] | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        small_model: bool = False,
        villani_mode: bool = False,
        villani_objective: str | None = None,
        benchmark_config: BenchmarkRuntimeConfig | None = None,
        debug_config: DebugConfig | None = None,
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
        self.auto_approve = auto_approve
        self.plan_mode = plan_mode
        self.max_repair_attempts = max_repair_attempts
        self.approval_callback = approval_callback or (lambda _n, _i: True)
        self._user_event_callback = event_callback or (lambda _event: None)
        self.small_model = small_model
        self.villani_mode = villani_mode
        self.villani_objective = villani_objective
        self.villani_config = VillaniModeConfig(
            enabled=villani_mode, steering_objective=villani_objective
        )
        self.benchmark_config = benchmark_config or BenchmarkRuntimeConfig()
        self._debug_config = debug_config or DebugConfig(mode=DebugMode.OFF)
        self._debug_recorder: DebugRecorder | None = None
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
                    "SubmitPlan(*)",
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
        self._last_failed_shell_command: dict[str, Any] | None = None
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
        self._last_validation_target = ""
        self._last_validation_summary = ""
        self._validation_repeated_without_new_evidence = False
        self._last_validation_artifact_signature = ""
        self._last_emitted_validation_fingerprint = ""
        self._failure_classifier = FailureClassifier()
        self._patch_sanity_retry_pending = False
        self._first_attempt_write_lock_active = False
        self._first_attempt_locked_target = ""
        self._context_governance = ContextGovernanceManager(self.repo)
        self._planning_read_only = False
        self._runtime_mode: Literal["execution", "planning"] = "execution"
        self._finalized_plan_artifact: dict[str, Any] | None = None
        self._verification_engine = VerificationEngine(self.repo)
        self._mission_id = ""
        self._mission_dir: Path | None = None
        self._mission_state: MissionState | None = None
        self._event_recorder: RuntimeEventRecorder | None = None
        self._current_turn_index: int | None = None
        if self.small_model:
            self._init_small_model_support()

    @property
    def event_callback(self) -> Callable[[dict[str, Any]], None]:
        return self._dispatch_event

    @event_callback.setter
    def event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._user_event_callback = callback or (lambda _event: None)

    def _debug_tool_callback(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._debug_recorder is None:
            return
        callback_turn_index = payload.get("turn_index")
        turn_index = callback_turn_index if isinstance(callback_turn_index, int) else self._current_turn_index
        if event_type == "file_read":
            self._debug_recorder.record_file_read(
                str(payload.get("file_path", "")),
                int(payload.get("size_bytes", 0)),
                bool(payload.get("ok", True)),
                str(payload.get("tool_call_id", "") or ""),
                turn_index=turn_index,
            )
            return
        if event_type == "file_write":
            self._debug_recorder.record_file_write(
                str(payload.get("file_path", "")),
                int(payload.get("size_bytes", 0)),
                bool(payload.get("ok", True)),
                str(payload.get("tool_call_id", "") or ""),
                turn_index=turn_index,
            )
            return
        if event_type == "patch_applied":
            self._debug_recorder.record_patch_applied(
                str(payload.get("file_path", "")),
                bool(payload.get("ok", True)),
                str(payload.get("tool_call_id", "") or ""),
                failure_reason=str(payload.get("failure_reason", "") or ""),
                hunks_attempted=payload.get("hunks_attempted"),
                hunks_failed=payload.get("hunks_failed"),
                turn_index=turn_index,
            )
            return
        if event_type == "command_started":
            self._debug_recorder.record_command_start(
                str(payload.get("command", "")),
                str(payload.get("cwd", ".")),
                str(payload.get("tool_call_id", "") or ""),
                turn_index=turn_index,
            )
            return
        if event_type == "command_finished":
            self._debug_recorder.record_command_finish(
                command=str(payload.get("command", "")),
                cwd=str(payload.get("cwd", ".")),
                exit_code=int(payload.get("exit_code", 0)),
                stdout=str(payload.get("stdout", "")),
                stderr=str(payload.get("stderr", "")),
                truncated=bool(payload.get("truncated", False)),
                tool_call_id=str(payload.get("tool_call_id", "") or ""),
                turn_index=turn_index,
            )


    def plan(self, instruction: str, answers: list[PlanAnswer] | None = None) -> PlanSessionResult:
        from villani_code.project_memory import load_repo_map, load_validation_config, scan_repo

        repo_map = load_repo_map(self.repo)
        if not repo_map:
            scanned_map, _, _ = scan_repo(self.repo)
            repo_map = scanned_map.to_dict()
        validation_steps = [step.name for step in load_validation_config(self.repo).steps]
        resolved_answers = list(answers or [])

        evidence_rows = _collect_planning_evidence(self.repo, instruction, repo_map)
        evidence_paths = [row["path"] for row in evidence_rows]

        self._planning_read_only = True
        self._runtime_mode = "planning"
        self._finalized_plan_artifact = None
        self._ensure_mission(instruction)
        self._update_mission_state(mode="planning", plan_summary=instruction)
        try:
            planning_prompt = build_planning_instruction(
                instruction,
                evidence_rows,
                validation_steps,
                resolved_answers,
            )
            run_result = self.run(planning_prompt, messages=build_initial_messages(self.repo, planning_prompt))
            artifact = copy.deepcopy(getattr(self, "_finalized_plan_artifact", None))
            if not isinstance(artifact, dict):
                artifact = _extract_strict_json_plan_artifact(run_result)
            if not isinstance(artifact, dict):
                assistant_text = _extract_assistant_plan_text(run_result)
                artifact = format_plan_text_to_artifact(instruction, assistant_text) if assistant_text else None
            if isinstance(artifact, dict):
                plan_result = _build_plan_result_from_artifact(instruction, artifact, resolved_answers, evidence_paths)
                if plan_result is not None:
                    if self._mission_dir is not None:
                        (self._mission_dir / "plan_artifact.json").write_text(json.dumps(plan_result.to_dict(), indent=2), encoding="utf-8")
                    self.event_callback({"type": "plan_finalized", "source": "runtime", "ready_to_execute": plan_result.ready_to_execute})
                    return plan_result
            message = "Planning failed: could not recover a structured plan from model output."
            self.event_callback(
                {
                    "type": "plan_failed",
                    "reason": "could_not_recover_structured_plan",
                    "message": message,
                    "ready_to_execute": False,
                }
            )
            raise RuntimeError(message)
        finally:
            self._planning_read_only = False
            self._runtime_mode = "execution"
            self._finalized_plan_artifact = None

    def run_with_plan(self, plan: PlanSessionResult) -> dict[str, Any]:
        if not plan.ready_to_execute:
            raise RuntimeError("Plan is not ready to execute; unresolved clarifications remain.")
        self._ensure_mission(plan.instruction)
        if self._mission_dir is not None:
            (self._mission_dir / "plan_artifact.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        self._update_mission_state(plan_summary=plan.task_summary)
        return self.run(plan.instruction, approved_plan=plan, force_task_mode=TaskMode.GENERAL)

    def run_villani_mode(self) -> dict[str, Any]:
        ensure_runtime_dependencies_not_shadowed(self.repo)
        self._ensure_mission(self.villani_objective or "Autonomous Villani mode run")
        controller = VillaniModeController(
            self,
            self.repo,
            steering_objective=self.villani_objective,
            event_callback=self.event_callback,
        )
        summary = controller.run()
        working = summary.get("working_memory", {}) if isinstance(summary, dict) else {}
        self._update_mission_state(
            mode="autonomous",
            status="completed",
            autonomous_wave=int(summary.get("waves", 0) or 0) if isinstance(summary, dict) else 0,
            autonomous_backlog_summary=[str(v) for v in summary.get("recommended_next_steps", [])[:8]] if isinstance(summary, dict) else [],
            autonomous_attempted_tasks=len(summary.get("attempted", [])) if isinstance(summary, dict) else 0,
            autonomous_satisfied_keys_summary=[str(k) for k in (working.get("satisfied_task_keys", {}) or {}).keys()][:8],
            autonomous_blockers_summary=[str(v) for v in (working.get("stop_decision_rationale", {}) or {}).values()][:8],
            autonomous_stop_reason=str(summary.get("done_reason", "")) if isinstance(summary, dict) else "",
        )
        if self._event_recorder is not None:
            self._event_recorder.write_digest()
        if self._debug_recorder is not None:
            self._debug_recorder.record_event("subagent_finished", "Autonomous run finished", {"done_reason": summary.get("done_reason", "") if isinstance(summary, dict) else ""})
            self._debug_recorder.write_final_summary(
                status="completed",
                termination_reason="completed",
                total_turns=0,
                mission_id=self._mission_id,
            )
        text = VillaniModeController.format_summary(summary)
        response = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        return {"response": response, "summary": summary}

    def run(
        self,
        instruction: str,
        messages: list[dict[str, Any]] | None = None,
        execution_budget: ExecutionBudget | None = None,
        inject_projected_context: bool = False,
        force_task_mode: TaskMode | None = None,
        approved_plan: PlanSessionResult | None = None,
    ) -> dict[str, Any]:
        if approved_plan is not None and not approved_plan.ready_to_execute:
            raise RuntimeError("Approved plan is not ready to execute; unresolved clarifications remain.")
        self._ensure_mission(instruction)
        messages = messages or build_initial_messages(self.repo, instruction)
        if approved_plan is not None:
            if self._mission_dir is not None:
                (self._mission_dir / "plan_artifact.json").write_text(json.dumps(approved_plan.to_dict(), indent=2), encoding="utf-8")
            self._update_mission_state(plan_summary=approved_plan.task_summary)
            approved_plan_block = {"type": "text", "text": build_approved_plan_context(approved_plan)}
            if messages and messages[0].get("role") == "user" and isinstance(messages[0].get("content"), list):
                messages[0]["content"].append(approved_plan_block)
            else:
                messages.append({"role": "user", "content": [approved_plan_block]})
        if inject_projected_context:
            self._inject_projected_context(messages)
        if force_task_mode is not None:
            self._task_mode = force_task_mode
        elif approved_plan is not None:
            self._task_mode = TaskMode.GENERAL
        elif self._runtime_mode == "planning":
            self._task_mode = TaskMode.INSPECT_AND_PLAN
        else:
            self._ensure_project_memory_and_plan(instruction)
            self._task_mode = classify_task_mode(instruction)
        diagnosis = None
        diagnosed_target_file = ""
        required_initial_read = ""
        initial_read_enforced = False
        pre_edit_failure_evidence = None
        diagnosis_confidence = "weak"
        if self.small_model or self.villani_mode or self.benchmark_config.enabled:
            try:
                from villani_code import state_runtime

                pre_edit_failure_evidence = state_runtime.run_pre_edit_failure_localization(self)
                diagnosis = state_runtime.run_pre_edit_diagnosis(
                    self,
                    instruction,
                    failure_evidence=pre_edit_failure_evidence,
                )
            except Exception:
                diagnosis = None
            if diagnosis:
                from villani_code import state_runtime

                state_runtime.inject_diagnosis_hint(messages, diagnosis)
                diagnosed_target_file = str(diagnosis.get("target_file", "")).strip().replace("\\", "/").lstrip("./")
                diagnosis_confidence = state_runtime.classify_diagnosis_target_confidence(
                    self,
                    diagnosis,
                    failure_evidence=pre_edit_failure_evidence,
                )
                target_path = (self.repo / diagnosed_target_file).resolve() if diagnosed_target_file else None
                repo_root = self.repo.resolve()
                if (
                    diagnosis_confidence == "strong"
                    and diagnosed_target_file
                    and target_path is not None
                    and is_path_within(repo_root, target_path)
                    and target_path.exists()
                    and target_path.is_file()
                ):
                    required_initial_read = diagnosed_target_file
        tools = tool_specs()
        transcript: dict[str, Any] = {
            "requests": [],
            "responses": [],
            "tool_invocations": [],
            "tool_results": [],
            "streamed_events_count": 0,
        }
        self.event_callback(
            {
                "type": "diagnosis_target_forced_read",
                "target_file": diagnosed_target_file,
                "target_found": bool(diagnosed_target_file),
                "confidence": diagnosis_confidence,
                "enforced": bool(required_initial_read),
            }
        )
        if diagnosed_target_file:
            self.event_callback(
                {
                    "type": "diagnosis_target_forced" if required_initial_read else "diagnosis_target_hint_only",
                    "target_file": diagnosed_target_file,
                    "confidence": diagnosis_confidence,
                    "enforced": bool(required_initial_read),
                }
            )
        if self.benchmark_config.enabled:
            self.event_callback({
                "type": "benchmark_mode_enabled",
                "task_id": self.benchmark_config.task_id,
                "allowlist_paths": self.benchmark_config.allowlist_paths,
                "expected_files": self.benchmark_config.expected_files,
            })
        if required_initial_read:
            forced_tool_use_id = "forced-initial-read"
            forced_input = {"file_path": required_initial_read}
            forced_tool_use = {
                "type": "tool_use",
                "id": forced_tool_use_id,
                "name": "Read",
                "input": forced_input,
            }
            self.event_callback(
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": forced_input,
                    "tool_use_id": forced_tool_use_id,
                    "forced": True,
                }
            )
            forced_result = self._execute_tool_with_policy(
                "Read", forced_input, forced_tool_use_id, len(messages)
            )
            if self.small_model:
                forced_result = self._truncate_tool_result("Read", forced_result)
            if not forced_result.get("is_error"):
                self._files_read.add(required_initial_read)
            self.hooks.run_event(
                "PostToolUse",
                {
                    "event": "PostToolUse",
                    "tool": "Read",
                    "input": forced_input,
                    "result": forced_result,
                },
            )
            self.event_callback(
                {
                    "type": "tool_finished",
                    "name": "Read",
                    "input": forced_input,
                    "tool_use_id": forced_tool_use_id,
                    "is_error": forced_result["is_error"],
                    "forced": True,
                    "turn_index": self._current_turn_index if isinstance(self._current_turn_index, int) else 0,
                }
            )
            transcript["tool_invocations"].append(
                {"name": "Read", "input": forced_input, "id": forced_tool_use_id}
            )
            transcript["tool_results"].append(forced_result)
            messages.append({"role": "assistant", "content": [forced_tool_use]})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": forced_tool_use_id,
                            "content": forced_result["content"],
                            "is_error": forced_result["is_error"],
                        }
                    ],
                }
            )
            initial_read_enforced = True
        self._save_session_snapshot(messages)
        consecutive_empty_assistant_turns = 0
        empty_recovery_injected = False
        last_successful_concrete_action = "none yet"
        start = time.monotonic()
        turns_used = 0
        tool_calls_used = 1 if initial_read_enforced else 0
        consecutive_no_edit_turns = 0
        consecutive_recon_turns = 0
        benchmark_prose_only_after_forced_read = 0
        benchmark_forced_read_no_progress_guard_active = initial_read_enforced
        # Conservative benchmark-only fast-fail for repeated out-of-scope mutation attempts.
        benchmark_mutation_denials = 0
        benchmark_denial_limit = 3
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
        self._last_validation_target = ""
        self._last_validation_summary = ""
        self._validation_repeated_without_new_evidence = False
        self._last_validation_artifact_signature = ""
        self._last_emitted_validation_fingerprint = ""
        self._scope_expansion_used = False
        self._first_attempt_write_lock_active = bool(required_initial_read)
        self._first_attempt_locked_target = required_initial_read
        if self._first_attempt_write_lock_active:
            self.event_callback(
                {
                    "type": "first_attempt_write_locked",
                    "active": True,
                    "target_file": required_initial_read,
                }
            )

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

        def _assistant_text(blocks: list[dict[str, Any]]) -> str:
            return "\n".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )

        def _has_meaningful_assistant_text(blocks: list[dict[str, Any]]) -> bool:
            return bool(_assistant_text(blocks).strip())

        def _build_empty_turn_recovery_message() -> str:
            files_changed = _attributed_changed_files()
            files_fragment = ", ".join(files_changed[:4]) if files_changed else "none yet"
            if len(files_changed) > 4:
                files_fragment = f"{files_fragment}, +{len(files_changed) - 4} more"
            return (
                "RECOVERY: blank turns detected. "
                f"Objective: {instruction.strip() or 'complete the assigned mission'}. "
                f"Last concrete action: {last_successful_concrete_action}. "
                f"Changed files: {files_fragment}. "
                "Missing milestone: next verifiable step toward completion. "
                "Take exactly one concrete next action now and continue."
            )

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
            transcript_path = None
            if not self._planning_read_only:
                transcript_path = self._save_transcript_and_link(transcript)
            post = self._run_post_execution_validation(_change_summary()[2])
            if post:
                response.setdefault("content", []).append({"type": "text", "text": post})
            self._save_session_snapshot(messages)
            mission_status = "completed" if completed else ("interrupted" if reason in {"max_seconds", "max_turns", "max_tool_calls"} else "failed")
            self._update_mission_state(status=mission_status, changed_files=all_changes, compact_summary=summarize_mission_state(self._mission_state) if self._mission_state else "")
            if self._event_recorder is not None:
                self._event_recorder.write_digest()
            if self._debug_recorder is not None:
                self._debug_recorder.write_final_summary(
                    status=mission_status,
                    termination_reason=reason,
                    total_turns=turns_used,
                    mission_id=self._mission_id,
                )
            return {
                "response": response,
                "messages": messages,
                "transcript_path": str(transcript_path) if transcript_path is not None else "",
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
            self._current_turn_index = turns_used + 1
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
            if self._debug_recorder is not None:
                self._debug_recorder.record_turn_start(turns_used + 1, {"message_count": len(turn_messages)})
                self._debug_recorder.record_model_request(payload)
                self._debug_recorder.write_working_context(json.dumps(turn_messages, indent=2, ensure_ascii=False) + "\n")
            self.event_callback({"type": "model_request_started", "model": self.model})

            try:
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
            except Exception as exc:
                if self._debug_recorder is not None:
                    self._debug_recorder.record_model_request_failed(str(exc))
                    self._debug_recorder.record_turn_finish(turns_used + 1, "model_request_failed")
                raise

            response["content"] = normalize_content_blocks(response.get("content"))
            transcript["responses"].append(response)
            if self._debug_recorder is not None:
                self._debug_recorder.record_model_response(response)
                self._debug_recorder.record_turn_finish(turns_used + 1, str(response.get("stop_reason", "")))
            messages.append(
                {"role": "assistant", "content": response.get("content", [])}
            )
            turns_used += 1

            content_blocks = response.get("content", [])
            tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
            has_meaningful_text = _has_meaningful_assistant_text(content_blocks)
            empty = is_effectively_empty_content(content_blocks)
            empty_assistant_turn = not tool_uses and not has_meaningful_text
            if empty_assistant_turn:
                consecutive_empty_assistant_turns += 1
                if empty_recovery_injected:
                    return _finish_bounded(response, "stalled_empty_responses", False)
                if consecutive_empty_assistant_turns >= 2:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": _build_empty_turn_recovery_message()}
                            ],
                        }
                    )
                    empty_recovery_injected = True
                    reason = _budget_reason()
                    if reason:
                        return _finish_bounded(response, reason, reason == "completed")
                    continue
                reason = _budget_reason()
                if reason:
                    return _finish_bounded(response, reason, reason == "completed")
                continue
            else:
                consecutive_empty_assistant_turns = 0
                empty_recovery_injected = False
                if tool_uses:
                    last_successful_concrete_action = (
                        f"assistant emitted {len(tool_uses)} tool call(s)"
                    )
                elif has_meaningful_text:
                    last_successful_concrete_action = "assistant produced substantive text output"
            if benchmark_forced_read_no_progress_guard_active and tool_uses:
                benchmark_forced_read_no_progress_guard_active = False
            if not tool_uses:
                only_textual_response = bool(content_blocks) and all(
                    isinstance(block, dict) and block.get("type") == "text"
                    for block in content_blocks
                )
                prose_only_non_progress = (
                    self.benchmark_config.enabled
                    and benchmark_forced_read_no_progress_guard_active
                    and not _has_meaningful_benchmark_edit()
                    and (empty or only_textual_response)
                )
                if prose_only_non_progress:
                    benchmark_prose_only_after_forced_read += 1
                    self.event_callback(
                        {
                            "type": "benchmark_prose_only_after_forced_read",
                            "task_id": self.benchmark_config.task_id,
                            "attempt": benchmark_prose_only_after_forced_read,
                        }
                    )
                    if benchmark_prose_only_after_forced_read >= 2:
                        self.event_callback(
                            {
                                "type": "benchmark_no_progress_after_forced_read",
                                "task_id": self.benchmark_config.task_id,
                            }
                        )
                        return _finish_bounded(
                            response, "benchmark_no_progress_after_forced_read", False
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Benchmark mode: no prose-only turns. Make exactly one concrete next tool call.",
                                }
                            ],
                        }
                    )
                    continue
                if empty:
                    if (
                        self.benchmark_config.enabled
                        and not benchmark_forced_read_no_progress_guard_active
                        and not _has_meaningful_benchmark_edit()
                    ):
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
                    transcript_path = self._save_transcript_and_link(transcript)
                    post = self._run_post_execution_validation(_change_summary()[2])
                    if post:
                        response.setdefault("content", []).append({"type": "text", "text": post})
                    self._save_session_snapshot(messages)
                    if self._event_recorder is not None:
                        self._event_recorder.write_digest()
                    if self._debug_recorder is not None:
                        self._debug_recorder.write_final_summary(
                            status="completed",
                            termination_reason="completed",
                            total_turns=turns_used,
                            mission_id=self._mission_id,
                        )
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
                if (
                    self.benchmark_config.enabled
                    and not benchmark_forced_read_no_progress_guard_active
                    and not _has_meaningful_benchmark_edit()
                ):
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
                transcript_path = None
                if not self._planning_read_only:
                    transcript_path = self._save_transcript_and_link(transcript)
                    self._save_session_snapshot(messages)
                self._update_mission_state(status="completed", compact_summary=summarize_mission_state(self._mission_state) if self._mission_state else "")
                if self._event_recorder is not None:
                    self._event_recorder.write_digest()
                if self._debug_recorder is not None:
                    self._debug_recorder.write_final_summary(
                        status="completed",
                        termination_reason="completed",
                        total_turns=turns_used,
                        mission_id=self._mission_id,
                    )
                return {
                    "response": response,
                    "messages": messages,
                    "transcript_path": str(transcript_path) if transcript_path is not None else "",
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

                if self._runtime_mode == "planning" and tool_name == "SubmitPlan":
                    self._finalized_plan_artifact = copy.deepcopy(tool_input)
                    self.event_callback({"type": "plan_artifact_submitted"})
                    response = {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "Plan finalized.",
                            }
                        ],
                    }
                    transcript["responses"].append(response)
                    return {
                        "response": response,
                        "messages": messages,
                        "transcript_path": "",
                        "transcript": transcript,
                    }

                result = self._execute_tool_with_policy(
                    tool_name, tool_input, tool_use_id, len(messages)
                )
                tool_calls_used += 1
                if self.small_model:
                    result = self._truncate_tool_result(tool_name, result)
                    if tool_name == "Read" and not result.get("is_error"):
                        self._files_read.add(str(tool_input.get("file_path", "")))

                if tool_name in {"Write", "Patch"} and not result.get("is_error"):
                    self._pending_verification = self._run_post_edit_verification(
                        trigger=f"{tool_name} execution"
                    )
                elif tool_name == "Bash":
                    self._pending_verification = self._run_verification(
                        trigger=f"{tool_name} execution"
                    )

                if result.get("is_error"):
                    result_text = str(result.get("content", ""))
                    self._update_mission_state(last_failed_command=f"{tool_name} {tool_input}", last_failed_summary=result_text[:500])
                    if (
                        self.benchmark_config.enabled
                        and tool_name in {"Write", "Patch"}
                        and "Benchmark policy blocked this mutation" in result_text
                    ):
                        benchmark_mutation_denials += 1
                        self.event_callback(
                            {
                                "type": "benchmark_mutation_denial_observed",
                                "task_id": self.benchmark_config.task_id,
                                "count": benchmark_mutation_denials,
                                "limit": benchmark_denial_limit,
                            }
                        )
                        if benchmark_mutation_denials >= benchmark_denial_limit and not _has_meaningful_benchmark_edit():
                            self.event_callback(
                                {
                                    "type": "benchmark_repeated_mutation_denials",
                                    "task_id": self.benchmark_config.task_id,
                                    "count": benchmark_mutation_denials,
                                    "limit": benchmark_denial_limit,
                                }
                            )
                            return _finish_bounded(response, "benchmark_repeated_mutation_denials", False)
                    failure = self._failure_classifier.classify(
                        f"{tool_name} failed", result_text
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
                        "turn_index": self._current_turn_index if isinstance(self._current_turn_index, int) else turns_used,
                    }
                )
                transcript["tool_invocations"].append(
                    {"name": tool_name, "input": tool_input, "id": tool_use_id}
                )
                transcript["tool_results"].append(result)
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
                consecutive_empty_assistant_turns = 0
                empty_recovery_injected = False
                last_successful_concrete_action = (
                    f"executed tool(s): {', '.join(str(b.get('name', '')) for b in tool_uses)}"
                )
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
                    benchmark_mutation_denials = 0
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

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        if "turn_index" not in event and isinstance(self._current_turn_index, int):
            event = {**event, "turn_index": self._current_turn_index}
        if self._event_recorder is not None:
            self._event_recorder.record(event)
        if self._debug_recorder is not None:
            self._debug_recorder.on_runner_event(event)
        self._user_event_callback(event)

    def _ensure_mission(self, instruction: str) -> None:
        mode = "autonomous" if self.villani_mode else self._runtime_mode
        if self._mission_state is None:
            self._mission_state = create_mission_state(self.repo, instruction, mode=mode)
            self._mission_id = self._mission_state.mission_id
            self._mission_dir = get_mission_dir(self.repo, self._mission_id)
            self._event_recorder = RuntimeEventRecorder(self._mission_dir)
            if self._debug_config.enabled:
                self._debug_recorder = DebugRecorder(
                    config=self._debug_config,
                    run_id=self._mission_id,
                    objective=instruction,
                    repo=self.repo,
                    mode=mode,
                    model=self.model,
                )
        self._update_mission_state(objective=instruction, mode=mode, status="active")

    def _update_mission_state(self, **fields: Any) -> None:
        if self._mission_state is None:
            return
        for key, value in fields.items():
            if hasattr(self._mission_state, key):
                setattr(self._mission_state, key, value)
        self._mission_state.intended_targets = sorted(self._intended_targets)
        if self._mission_state.status == "active":
            self._mission_state.changed_files = self._git_changed_files()
        save_mission_state(self.repo, self._mission_state)
        if self._debug_recorder is not None:
            self._debug_recorder.record_mission_state_snapshot(
                self._mission_state.to_dict(),
                "mission_state_update",
                turn_index=self._current_turn_index if isinstance(self._current_turn_index, int) else None,
            )

    def _inject_projected_context(self, messages: list[dict[str, Any]]) -> None:
        if not self._mission_state or self.benchmark_config.enabled:
            return
        if len(messages) <= 1:
            return
        if any(
            "Mission context packet:" in str(block.get("text", ""))
            for msg in messages
            for block in msg.get("content", [])
            if isinstance(block, dict)
        ):
            return
        packet = build_model_context_packet(self)
        rendered = render_model_context_packet(packet)
        from villani_code import state_runtime

        state_runtime.prepend_text_to_latest_safe_user_message(messages, rendered)

    def _save_transcript_and_link(self, transcript: dict[str, Any]) -> Path:
        path = save_transcript(self.repo, transcript, redact=self.redact)
        self._update_mission_state(last_transcript_path=str(path))
        return path

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

    def _build_tool_result_event_payload(
        self, tool_name: str, tool_use_id: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        content = result.get("content", "")
        is_error = bool(result.get("is_error", False))
        base_result_payload: dict[str, Any] = {
            "tool_use_id": tool_use_id,
            "tool_call_id": tool_use_id,
            "tool_name": tool_name,
            "is_error": is_error,
            "content": content,
        }
        payload: dict[str, Any] = dict(base_result_payload)
        payload["result_payload"] = dict(base_result_payload)
        if is_error:
            payload["error"] = {"error_type": "tool_error", "message": str(content)}
            payload["result_payload"]["error"] = payload["error"]
            return payload
        if tool_name == "Bash":
            try:
                decoded = json.loads(str(content))
            except Exception:
                decoded = {}
            if isinstance(decoded, dict):
                payload["command"] = decoded.get("command")
                payload["exit_code"] = decoded.get("exit_code")
                payload["stdout"] = decoded.get("stdout")
                payload["stderr"] = decoded.get("stderr")
                payload["result_payload"] = {
                    **base_result_payload,
                    "command": decoded.get("command"),
                    "exit_code": decoded.get("exit_code"),
                    "stdout": decoded.get("stdout"),
                    "stderr": decoded.get("stderr"),
                }
        elif tool_name == "Read":
            text_content = str(content)
            payload["result_payload"] = {
                **base_result_payload,
                "content": text_content,
                "bytes_read": len(text_content.encode("utf-8", errors="replace")),
                "preview": text_content[:240],
            }
        elif tool_name == "Write":
            text_content = str(content)
            payload["result_payload"] = {
                **base_result_payload,
                "content": text_content,
            }
        elif tool_name == "Patch":
            text_content = str(content)
            payload["result_payload"] = {
                **base_result_payload,
                "content": text_content,
            }
        return payload


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

    def _run_post_edit_verification(self, trigger: str = "edit") -> str:
        from villani_code import state_runtime

        return state_runtime.run_post_edit_verification(self, trigger)

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
