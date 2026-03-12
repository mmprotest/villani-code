from __future__ import annotations

import json
from dataclasses import asdict
import subprocess
import sys
from typing import Any

from villani_code.autonomy import VerificationStatus
from villani_code.indexing import DEFAULT_IGNORE, RepoIndex
from villani_code.live_display import apply_live_display_delta
from villani_code.planning import TaskMode, generate_execution_plan
from villani_code.project_memory import SessionState, ensure_project_memory, load_repo_map, update_session_state
from villani_code.context_governance import ContextCompactor, ContextInclusionReason, ContextExclusionReason
from villani_code.tools import execute_tool
from villani_code.validation_loop import run_validation
from villani_code.shells import baseline_import_validation_command, shell_family_for_platform
from villani_code.repair import execute_repair_loop
from villani_code.repo_map import build_repo_map
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.retrieval import Retriever
from villani_code.utils import ensure_dir


_DIAGNOSIS_KEYS = ("target_file", "bug_class", "fix_intent")


def parse_pre_edit_diagnosis(raw: Any) -> dict[str, str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if set(data.keys()) != set(_DIAGNOSIS_KEYS):
        return None
    cleaned: dict[str, str] = {}
    for key in _DIAGNOSIS_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        cleaned[key] = value.strip()
    return cleaned


def run_pre_edit_diagnosis(runner: Any, instruction: str) -> dict[str, str] | None:
    runner.event_callback({"type": "diagnosis_attempted"})
    evidence_lines = [f"Objective: {instruction.strip()}"]
    plan = getattr(runner, "_execution_plan", None)
    if plan is not None:
        if getattr(plan, "validation_steps", None):
            evidence_lines.append(
                "Verification: " + "; ".join(str(step) for step in plan.validation_steps[:3])
            )
        if getattr(plan, "relevant_files", None):
            evidence_lines.append(
                "Likely files: " + ", ".join(str(path) for path in plan.relevant_files[:5])
            )
    cfg = getattr(runner, "benchmark_config", None)
    if cfg and cfg.enabled:
        if cfg.visible_verification:
            evidence_lines.append("Visible verification: " + "; ".join(cfg.visible_verification[:3]))
        if cfg.expected_files:
            evidence_lines.append("Expected files: " + ", ".join(cfg.expected_files[:5]))

    system_prompt = (
        "Return strict JSON only with exactly these string keys: "
        'target_file, bug_class, fix_intent. No prose, no markdown, no extra keys.'
    )
    user_prompt = (
        "Produce one cheap pre-edit diagnosis from available evidence only. "
        "No tool calls, no repository exploration, no long reasoning.\n\n"
        + "\n".join(f"- {line}" for line in evidence_lines)
    )
    payload = {
        "model": runner.model,
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_prompt}]}],
        "system": [{"type": "text", "text": system_prompt}],
        "max_tokens": min(220, int(getattr(runner, "max_tokens", 220))),
        "stream": False,
    }
    try:
        response = runner.client.create_message(payload, stream=False)
    except Exception as exc:  # pragma: no cover - defensive path
        runner.event_callback({"type": "diagnosis_failed", "reason": f"call_error:{exc.__class__.__name__}"})
        return None

    blocks = response.get("content", []) if isinstance(response, dict) else []
    text = "\n".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )
    diagnosis = parse_pre_edit_diagnosis(text)
    if diagnosis is None:
        runner.event_callback({"type": "diagnosis_failed", "reason": "invalid_json"})
        return None
    runner.event_callback({"type": "diagnosis_generated", **diagnosis})
    return diagnosis


def inject_diagnosis_hint(messages: list[dict[str, Any]], diagnosis: dict[str, str]) -> None:
    hint = (
        "Likely diagnosis:\n"
        f"- Target file: {diagnosis['target_file']}\n"
        f"- Bug class: {diagnosis['bug_class']}\n"
        f"- Repair intent: {diagnosis['fix_intent']}\n\n"
        "Use this to focus your first inspection and first repair attempt. Treat it as a hint, not ground truth."
    )
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content.insert(0, {"type": "text", "text": hint})
        return
    messages.insert(0, {"role": "user", "content": [{"type": "text", "text": hint}]})


def prepare_messages_for_model(runner: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = [dict(m) for m in messages]
    if runner.small_model:
        inject_retrieval_briefing(runner, prepared)
        if runner._context_budget:
            prepared = runner._context_budget.compact(prepared)
    inventory = runner._context_governance.load_inventory()
    inventory.task_id = str(getattr(getattr(runner, "_execution_plan", None), "task_goal", "task"))[:80] or "task"
    total_chars = sum(len(str(m.get("content", ""))) for m in prepared)
    runner._context_governance.register_item(
        inventory,
        "messages.active",
        "messages",
        "prepared conversation messages",
        total_chars,
        ContextInclusionReason.TASK_RELEVANCE,
        "messages needed for current turn",
    )
    runner._context_governance.prune_for_budget(inventory)
    runner._context_governance.save_inventory(inventory)
    validate_anthropic_tool_sequence(prepared)
    return prepared


def validate_anthropic_tool_sequence(messages: list[dict[str, Any]]) -> None:
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        if not any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content):
            continue

        followup_index = index + 1
        if followup_index >= len(messages):
            raise RuntimeError(
                f"Invalid Anthropic tool sequence at message index {index}: assistant tool_use message must be immediately followed by a user tool_result message, but no follow-up message exists."
            )

        followup = messages[followup_index]
        if followup.get("role") != "user":
            raise RuntimeError(
                f"Invalid Anthropic tool sequence at message index {index}: assistant tool_use message must be immediately followed by a user tool_result message, but found role '{followup.get('role')}' at index {followup_index}."
            )

        followup_content = followup.get("content", [])
        if not isinstance(followup_content, list) or not followup_content:
            raise RuntimeError(
                f"Invalid Anthropic tool sequence at message index {index}: follow-up user message at index {followup_index} must contain a non-empty content list of tool_result blocks."
            )

        invalid_block_index = next(
            (
                block_index
                for block_index, block in enumerate(followup_content)
                if not (isinstance(block, dict) and block.get("type") == "tool_result")
            ),
            None,
        )
        if invalid_block_index is not None:
            raise RuntimeError(
                f"Invalid Anthropic tool sequence at message index {index}: follow-up user message at index {followup_index} must contain only tool_result blocks, but found non-tool_result block at content index {invalid_block_index}."
            )


def inject_retrieval_briefing(runner: Any, messages: list[dict[str, Any]]) -> None:
    if not runner._retriever or not messages:
        return
    last = messages[-1]
    if last.get("role") != "user":
        return
    content = last.get("content", [])
    if not isinstance(content, list):
        return
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return
    user_text = "\n".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    )
    if not user_text or "<retrieval-briefing>" in user_text:
        return
    hits = runner._retriever.query(user_text, k=8)
    if not hits:
        return
    briefing = "\n".join(f"- {h.path}: {h.reason}" for h in hits)
    content.insert(0, {"type": "text", "text": f"<retrieval-briefing>\n{briefing}\n</retrieval-briefing>"})


def init_small_model_support(runner: Any) -> None:
    index_path = runner.repo / ".villani_code" / "index" / "index.json"
    if index_path.exists():
        idx = RepoIndex.load(index_path)
        if idx.needs_rebuild(runner.repo):
            idx = RepoIndex.build(runner.repo, DEFAULT_IGNORE)
            idx.save(index_path)
            runner.event_callback({"type": "index_built", "path": str(index_path)})
        else:
            runner.event_callback({"type": "index_loaded", "path": str(index_path)})
    else:
        idx = RepoIndex.build(runner.repo, DEFAULT_IGNORE)
        idx.save(index_path)
        runner.event_callback({"type": "index_built", "path": str(index_path)})
    runner._retriever = Retriever(idx)
    runner._repo_map = build_repo_map(idx)


def _is_strongly_adjacent_path(candidate: str, locked_paths: set[str]) -> bool:
    c_norm = candidate.replace("\\", "/").lstrip("./")
    if not locked_paths:
        return False
    from pathlib import Path

    c_path = Path(c_norm)
    c_parent = str(c_path.parent)
    c_stem = c_path.stem
    for locked in locked_paths:
        l_norm = locked.replace("\\", "/").lstrip("./")
        l_path = Path(l_norm)
        if c_parent == str(l_path.parent):
            return True
        if c_path.name == "__init__.py" and c_parent == str(l_path.parent):
            return True
        if l_path.name == "__init__.py" and c_parent == str(l_path.parent):
            return True
        if c_stem == l_path.stem:
            return True
        if c_stem.startswith("test_") and c_stem[5:] == l_path.stem:
            return True
        if l_path.stem.startswith("test_") and l_path.stem[5:] == c_stem:
            return True
        if c_path.name == f"test_{l_path.stem}.py" or l_path.name == f"test_{c_stem}.py":
            return True
    return False


def small_model_tool_guard(runner: Any, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    constrained = runner.small_model or runner.villani_mode or runner.benchmark_config.enabled
    if not constrained:
        return None
    if tool_name in {"Write", "Patch"}:
        fp = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
        if fp:
            path = (runner.repo / fp).resolve()
            if is_ignored_repo_path(fp) or classify_repo_path(fp) != "authoritative":
                return f"Small-model mode policy: target path is not authoritative: {fp}."
            if tool_name == "Patch" and not path.exists():
                return f"Read-before-edit policy: cannot patch missing file {fp}. Use Write to create it first."
            if tool_name == "Write" and not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and fp not in runner._files_read:
                read_result = execute_tool("Read", {"file_path": fp, "max_bytes": 8000}, runner.repo, unsafe=runner.unsafe)
                if read_result.get("is_error"):
                    return f"Read-before-edit policy: failed to auto-read {fp}. Read it explicitly before editing."
                runner._files_read.add(fp)

            intended = set(getattr(runner, "_intended_targets", set()))
            if intended and fp not in intended:
                explicit_allowlisted = runner.benchmark_config.enabled and runner.benchmark_config.in_allowlist(fp)
                benchmark_scope_ok = (not runner.benchmark_config.enabled) or explicit_allowlisted
                has_evidence = (fp in runner._files_read) or _is_strongly_adjacent_path(fp, intended)
                can_expand_once = (
                    (not runner._scope_expansion_used)
                    and benchmark_scope_ok
                    and has_evidence
                    and classify_repo_path(fp) == "authoritative"
                    and not is_ignored_repo_path(fp)
                )
                if can_expand_once:
                    runner._scope_expansion_used = True
                elif explicit_allowlisted:
                    pass
                else:
                    if runner._scope_expansion_used:
                        reason = "scope expansion already consumed"
                    elif not benchmark_scope_ok:
                        reason = "target is outside benchmark allowlist"
                    else:
                        reason = "target lacks prior read evidence or strong adjacency"
                    runner.event_callback(
                        {
                            "type": "small_model_scope_blocked",
                            "file_path": fp,
                            "intended_targets": sorted(intended),
                            "reason": reason,
                        }
                    )
                    return (
                        f"Constrained scope lock: blocked widening to {fp}; {reason}. "
                        f"Locked targets: {sorted(intended)}."
                    )

            if path.exists() and path.is_file() and fp not in runner._before_contents:
                before_text = path.read_text(encoding="utf-8", errors="replace")
                runner._before_contents[fp] = before_text
                if fp in getattr(runner, "_current_verification_targets", set()):
                    runner._current_verification_before_contents.setdefault(fp, before_text)
    if tool_name == "Write":
        file_path = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
        path = (runner.repo / file_path).resolve()
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > 10_000 or len(text.splitlines()) > 200:
                return "Small-model mode policy: avoid whole-file writes for large files; use Patch instead."
    return None

def tighten_tool_input(tool_name: str, tool_input: dict[str, Any]) -> None:
    if tool_name == "Read":
        tool_input["max_bytes"] = min(int(tool_input.get("max_bytes", 200000)), 50_000)
    if tool_name == "Grep":
        tool_input["max_results"] = min(int(tool_input.get("max_results", 200)), 60)


def truncate_tool_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("is_error"):
        return result
    content = str(result.get("content", ""))
    if tool_name == "Bash" and len(content) > 6000:
        result["content"] = content[:2000] + "\n...\n" + content[-3000:]
    elif len(content) > 50000:
        result["content"] = content[:50000]
    return result


def git_changed_files(repo: Any) -> list[str]:
    proc = subprocess.run(["git", "status", "--short"], cwd=repo, capture_output=True, text=True)
    return [line[3:].strip() for line in proc.stdout.splitlines() if line.strip()]


def run_verification(runner: Any, trigger: str = "edit") -> str:
    current_changed = set(git_changed_files(runner.repo))
    attributed_changed = sorted(current_changed - runner._verification_baseline_changed)
    attributed_intentional: list[str] = []
    attributed_incidental: list[str] = []
    for path in attributed_changed:
        if is_ignored_repo_path(path) or classify_repo_path(path) != "authoritative":
            attributed_incidental.append(path)
        else:
            attributed_intentional.append(path)

    commands: list[list[str]] = []
    if attributed_intentional:
        commands.append(["git", "diff", "--stat", "--", *attributed_intentional])
        commands.append(["git", "diff", "--", *attributed_intentional])

    touched_tests = [p for p in attributed_intentional if p.startswith("tests/") and p.endswith(".py")]
    touched_sources = [p for p in attributed_intentional if p.endswith(".py") and not p.startswith("tests/")]
    task_mode = getattr(runner, "_task_mode", TaskMode.GENERAL)
    if touched_tests:
        commands.append(["pytest", "-q", *touched_tests])
    elif touched_sources:
        family = shell_family_for_platform(sys.platform)
        commands.append(["bash", "-lc", baseline_import_validation_command(family)])
    elif task_mode in {TaskMode.DOCS_UPDATE_SAFE, TaskMode.INSPECT_AND_PLAN}:
        pass

    lines = ["<verification>", f"trigger: {trigger}"]
    if runner._intended_targets and not attributed_intentional:
        lines.append(f"locked_targets: {json.dumps(sorted(runner._intended_targets))}")
        lines.append("note: no intentional diff is currently attributable in locked scope")
        lines.append("next: inspect locked file, produce one bounded patch, or stop")
    cmd_results: list[dict[str, Any]] = []
    for cmd in commands:
        proc = subprocess.run(cmd, cwd=runner.repo, capture_output=True, text=True)
        stderr_lines = "\n".join([ln for ln in proc.stderr.splitlines() if ln][:5])
        stdout = proc.stdout[:1500]
        cmd_results.append(
            {
                "command": " ".join(cmd),
                "exit": proc.returncode,
                "stdout": stdout,
                "stderr": stderr_lines,
            }
        )
        lines.append(f"command: {' '.join(cmd)}")
        lines.append(f"exit: {proc.returncode}")
        if stdout:
            lines.append(f"stdout:\n{stdout}")
        if stderr_lines:
            lines.append(f"key stderr:\n{stderr_lines}")

    verification_artifacts = [
        r.get("command", "") for r in cmd_results if int(r.get("exit", 1)) == 0
    ]
    verification = runner._verification_engine.verify(
        trigger,
        attributed_intentional,
        cmd_results,
        validation_artifacts=verification_artifacts,
        intended_targets=sorted(runner._current_verification_targets),
        before_contents=dict(runner._current_verification_before_contents),
    )
    finding_fingerprints = sorted(
        "|".join(
            [
                f.category.value,
                (f.file_path or "").replace("\\", "/").lstrip("./"),
                f.message.strip().lower(),
            ]
        )
        for f in verification.findings
    )
    fingerprint = json.dumps(
        {
            "status": verification.status.value,
            "findings": finding_fingerprints,
            "intentional": sorted(attributed_intentional),
            "validation_artifact_count": len(verification_artifacts),
        },
        sort_keys=True,
    )
    repeated_stale = (
        runner._last_verification_fingerprint == fingerprint
        and set(attributed_intentional) == runner._last_verification_intentional
        and len(verification_artifacts) == runner._last_verification_artifact_count
    )
    if repeated_stale:
        runner._repeated_stale_verification_count += 1
    else:
        runner._repeated_stale_verification_count = 0
        runner._last_verification_fingerprint = fingerprint
        runner._last_verification_intentional = set(attributed_intentional)
        runner._last_verification_artifact_count = len(verification_artifacts)

    if repeated_stale and runner._repeated_stale_verification_count >= 2:
        runner.event_callback(
            {
                "type": "failure_classified",
                "category": "repeated_no_progress",
                "summary": "repeated identical verification state with no new evidence",
                "next_strategy": "Change strategy or stop this task in budgeted mode.",
                "occurrence": runner._repeated_stale_verification_count,
            }
        )
        return (
            "<verification>\n"
            "verification state repeated\n"
            "no new evidence was produced\n"
            "next step must either change target, change validation evidence, or stop\n"
            "</verification>"
        )

    lines.append(f"intentional_changed: {json.dumps(sorted(attributed_intentional))}")
    if attributed_incidental:
        lines.append(f"incidental_changed: {json.dumps(sorted(attributed_incidental))}")
    lines.append(f"status: {verification.status.value}")
    lines.append(f"confidence: {verification.confidence_score}")
    if verification.findings:
        lines.append("findings:")
        for finding in verification.findings[:6]:
            lines.append(f"- {finding.category.value}: {finding.message}")
    lines.append("</verification>")
    runner.event_callback(
        {
            "type": "verification_ran",
            "status": verification.status.value,
            "confidence": verification.confidence_score,
            "repeated_stale_state": repeated_stale,
        }
    )
    if verification.status in {VerificationStatus.FAIL, VerificationStatus.UNCERTAIN}:
        runner.event_callback(
            {
                "type": "confidence_risk",
                "confidence": verification.confidence_score,
                "risk": "medium"
                if verification.status == VerificationStatus.UNCERTAIN
                else "high",
                "summary": verification.summary,
            }
        )
    return "\n".join(lines)


def emit_policy_event(
    runner: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    decision: Any,
    reason: str,
) -> None:
    runner.event_callback(
        {
            "type": "policy_decision",
            "name": tool_name,
            "input": tool_input,
            "decision": getattr(decision, "value", str(decision)),
            "reason": reason,
        }
    )


def capture_edit_proposal(runner: Any, response: dict[str, Any]):
    from villani_code.patch_apply import extract_unified_diff_targets

    text_blocks = [
        block.get("text", "")
        for block in response.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    diff_text = "\n".join([t for t in text_blocks if "--- a/" in t and "+++ b/" in t])
    if not diff_text:
        return None
    files = extract_unified_diff_targets(diff_text)
    return runner.proposals.create(diff_text=diff_text, files_touched=files, summary="Assistant proposed unified diff")

def is_no_progress_response(response: dict[str, Any]) -> bool:
    blocks = response.get("content", [])
    text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if not text:
        return True
    return len(text) <= 2


def save_session_snapshot(runner: Any, messages: list[dict[str, Any]]) -> None:
    root = runner.repo / ".villani_code" / "sessions"
    ensure_dir(root)
    (root / "last.json").write_text(
        json.dumps({"id": "last", "messages": messages, "cwd": str(runner.repo), "settings": {"model": runner.model}}, indent=2),
        encoding="utf-8",
    )


def render_stream_event(runner: Any, event: dict[str, Any]) -> None:
    if event.get("type") == "message_stop":
        tail = runner._coalescer.flush()
        if tail:
            if runner.print_stream:
                print(tail, end="", flush=True)
            else:
                runner.event_callback({"type": "stream_text", "text": tail})
        return
    if event.get("type") != "content_block_delta":
        return
    delta = event.get("delta", {})
    if delta.get("type") == "text_delta":
        raw_text = delta.get("text", "")
        before = runner._live_stream_buffer
        runner._live_stream_buffer, updated_started = apply_live_display_delta(before, raw_text, runner._live_stream_started)
        if updated_started and not runner._live_stream_started:
            runner.event_callback({"type": "first_text_delta"})
        runner._live_stream_started = updated_started
        appended = runner._live_stream_buffer[len(before):]
        if appended:
            emit = runner._coalescer.consume(appended)
            if emit:
                if runner.print_stream:
                    print(emit, end="", flush=True)
                else:
                    runner.event_callback({"type": "stream_text", "text": emit})
    if runner.verbose and delta.get("type") == "input_json_delta":
        partial = f"[dim]tool delta: {delta.get('partial_json', '')[:200]}[/dim]"
        if runner.print_stream:
            runner.console.print(partial)
        else:
            runner.event_callback({"type": "stream_text", "text": partial})




def _build_session_state_from_plan(instruction: str, plan: Any) -> SessionState:
    return SessionState(
        task_summary=instruction[:220],
        plan_summary=plan.task_goal[:220],
        plan_risk=plan.risk_level.value,
        grounding_evidence_summary=list(plan.grounding_evidence.get("explicit_signals", []))[:6] if isinstance(plan.grounding_evidence, dict) else [],
        action_classes=list(plan.action_classes),
        estimated_scope=plan.estimated_scope,
        change_impact=str(getattr(plan, "change_impact", "source_only")),
        task_mode=str(getattr(plan, "task_mode", TaskMode.GENERAL.value)),
        candidate_targets_summary=[str(v.get("target", "")) for v in getattr(plan, "candidate_targets", [])[:8]],
        validation_plan_summary=list(plan.validation_steps[:6]),
        outcome_status="planned",
        next_step_hints=["Execute scoped edits", "Run targeted validation", "Escalate validation when required"],
        handoff_checkpoint=f"risk={plan.risk_level.value};scope={plan.estimated_scope};impact={getattr(plan, 'change_impact', 'source_only')}",
    )


def ensure_project_memory_and_plan(runner: Any, instruction: str) -> None:
    ensure_project_memory(runner.repo)
    runner.event_callback({"type": "init_started"})
    runner.event_callback({"type": "init_completed", "path": str(runner.repo / ".villani")})
    runner.event_callback({"type": "planning_started"})

    repo_map = load_repo_map(runner.repo)
    validation_steps: list[str] = []
    val_file = runner.repo / ".villani" / "validation.json"
    if val_file.exists():
        try:
            payload = json.loads(val_file.read_text(encoding="utf-8"))
            validation_steps = [str(s.get("name", "")) for s in payload.get("steps", []) if isinstance(s, dict)]
        except json.JSONDecodeError:
            validation_steps = []

    plan = generate_execution_plan(instruction, runner.repo, repo_map, validation_steps)
    runner._execution_plan = plan
    inventory = runner._context_governance.load_inventory()
    inventory.task_id = instruction[:80] or "task"
    runner._context_governance.register_item(
        inventory,
        ".villani/repo_map.json",
        "memory",
        "repo map loaded",
        len(json.dumps(repo_map)),
        ContextInclusionReason.MEMORY_SIGNAL,
        "planning requires repo memory",
    )
    runner._context_governance.register_item(
        inventory,
        ".villani/validation.json",
        "memory",
        "validation config loaded",
        sum(len(v) for v in validation_steps),
        ContextInclusionReason.MEMORY_SIGNAL,
        "planning requires validation hints",
    )
    stale = runner._context_governance.detect_stale_context(inventory, plan.task_mode, 0)
    for sig in stale:
        runner._context_governance.exclude_candidate(inventory, f"stale:{sig}", "stale", sig, 120, ContextExclusionReason.STALE, "stale context detected")
    runner._context_governance.prune_for_budget(inventory)
    runner._context_governance.save_inventory(inventory)
    runner.event_callback({"type": "plan_generated", "plan": plan.to_dict(), "human": plan.to_human_text()})
    runner.event_callback({"type": "plan_risk_rationale", "risk": plan.risk_level.value, "drivers": plan.risk_assessment.get("drivers", [])})

    session = _build_session_state_from_plan(instruction, plan)

    if runner.plan_mode == "off" or not plan.non_trivial:
        runner.event_callback({"type": "plan_auto_approved", "risk": plan.risk_level.value})
        update_session_state(runner.repo, session)
        return

    if runner.villani_mode:
        runner.event_callback({"type": "plan_auto_approved", "risk": plan.risk_level.value})
        update_session_state(runner.repo, session)
        return

    runner.event_callback({"type": "plan_approval_required", "risk": plan.risk_level.value})
    approved = runner.approval_callback("ExecutionPlan", {"summary": plan.to_human_text(), "risk": plan.risk_level.value})
    if not approved:
        runner.event_callback({"type": "plan_rejected"})
        session.outcome_status = "rejected"
        session.next_step_hints = ["Revise plan scope or lower risk before retrying"]
        update_session_state(runner.repo, session)
        raise RuntimeError("Execution plan rejected by user.")
    runner.event_callback({"type": "plan_approved", "risk": plan.risk_level.value})
    update_session_state(runner.repo, session)




def run_post_execution_validation(runner: Any, changed_files: list[str]) -> str:
    if not changed_files:
        return ""
    plan = getattr(runner, "_execution_plan", None)
    plan_impact = getattr(plan, "change_impact", None)
    plan_actions = list(getattr(plan, "action_classes", [])) if plan else []
    repo_map = load_repo_map(runner.repo)

    runner.event_callback({"type": "validation_started", "changed_files": changed_files})
    task_mode = str(getattr(plan, "task_mode", TaskMode.GENERAL.value))
    result = run_validation(runner.repo, changed_files, event_callback=runner.event_callback, repo_map=repo_map, change_impact=plan_impact, action_classes=plan_actions, task_mode=task_mode)
    runner.event_callback({
        "type": "validation_plan_selected",
        "steps": [s.step.name for s in result.plan.selected_steps],
        "reasons": [r.reason for r in result.plan.reasons[:6]],
        "escalation": result.plan.escalation.reason,
    })
    inventory = runner._context_governance.load_inventory()
    compact_validation = ContextCompactor.compact_validation_logs(result.failure_summary if not result.passed else "Validation passed")
    inventory.compactions.append(compact_validation)
    runner._context_governance.register_item(
        inventory,
        "validation.summary",
        "validation",
        "latest validation summary",
        compact_validation.compacted_units,
        ContextInclusionReason.VALIDATION_SIGNAL,
        "validation outcomes affect next step",
    )
    stale = runner._context_governance.detect_stale_context(inventory, task_mode, len(getattr(result, "steps", [])))
    if stale:
        runner.event_callback({"type": "context_stale_detected", "signals": stale})
    runner._context_governance.prune_for_budget(inventory)
    runner._context_governance.save_inventory(inventory)

    if result.passed:
        checkpoint = runner._context_governance.create_checkpoint(inventory, str(getattr(plan, "task_goal", "")), ["validation passed"])
        runner.event_callback({"type": "context_checkpoint_created", "checkpoint_id": checkpoint.checkpoint_id, "reason": "validation_passed"})
        update_session_state(runner.repo, SessionState(
            affected_files=changed_files,
            validation_plan_summary=[s.step.name for s in result.plan.selected_steps],
            validation_summary="passed",
            outcome_status="success",
            next_step_hints=["Finalize and report output"],
            handoff_checkpoint="validation_passed",
        ))
        return "Validation: passed."

    outcome = execute_repair_loop(
        runner=runner,
        repo=runner.repo,
        changed_files=changed_files,
        initial_validation=result,
        repo_map=repo_map,
        change_impact=plan_impact,
        action_classes=plan_actions,
        max_attempts=int(getattr(runner, "max_repair_attempts", 2)),
    )
    if outcome.recovered:
        checkpoint = runner._context_governance.create_checkpoint(inventory, str(getattr(plan, "task_goal", "")), ["validation passed after repair"])
        runner.event_callback({"type": "context_checkpoint_created", "checkpoint_id": checkpoint.checkpoint_id, "reason": "repair_recovered"})
        update_session_state(runner.repo, SessionState(
            affected_files=changed_files,
            validation_plan_summary=[s.step.name for s in result.plan.selected_steps],
            validation_summary="passed after repair",
            repair_attempt_summaries=[asdict(a) for a in outcome.attempts],
            outcome_status="recovered",
            next_step_hints=["Report repaired validation and summarize edits"],
            handoff_checkpoint="repair_recovered",
        ))
        return outcome.message

    checkpoint = runner._context_governance.create_checkpoint(inventory, str(getattr(plan, "task_goal", "")), ["repair attempts exhausted"])
    runner.event_callback({"type": "context_checkpoint_created", "checkpoint_id": checkpoint.checkpoint_id, "reason": "repair_exhausted"})
    update_session_state(runner.repo, SessionState(
        affected_files=changed_files,
        validation_plan_summary=[s.step.name for s in result.plan.selected_steps],
        validation_summary="failed",
        last_failed_step=outcome.last_failed_step,
        repair_attempt_summaries=[asdict(a) for a in outcome.attempts],
        outcome_status="failed",
        next_step_hints=["Inspect failing step and rerun with interactive guidance"],
        handoff_checkpoint="repair_exhausted",
    ))
    return outcome.message
