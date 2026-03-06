from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any

from villani_code.autonomy import VerificationStatus
from villani_code.edits import ProposalStore
from villani_code.indexing import DEFAULT_IGNORE, RepoIndex
from villani_code.live_display import apply_live_display_delta
from villani_code.planning import PlanRiskLevel, generate_execution_plan
from villani_code.project_memory import SessionState, ensure_project_memory, load_repo_map, update_session_state
from villani_code.prompting import build_initial_messages, build_system_blocks
from villani_code.tools import execute_tool, tool_specs
from villani_code.validation_loop import run_validation
from villani_code.repo_map import build_repo_map
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.retrieval import Retriever
from villani_code.utils import ensure_dir


def prepare_messages_for_model(runner: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = [dict(m) for m in messages]
    if runner.small_model:
        inject_retrieval_briefing(runner, prepared)
        if runner._context_budget:
            prepared = runner._context_budget.compact(prepared)
    return prepared


def inject_retrieval_briefing(runner: Any, messages: list[dict[str, Any]]) -> None:
    if not runner._retriever or not messages:
        return
    last = messages[-1]
    if last.get("role") != "user":
        return
    content = last.get("content", [])
    if not isinstance(content, list):
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


def small_model_tool_guard(runner: Any, tool_name: str, tool_input: dict[str, Any]) -> str | None:
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
    if (runner.repo / "tests").exists() and attributed_intentional:
        commands.append(["pytest", "-q", "tests/test_runner_defaults.py"])

    lines = ["<verification>", f"trigger: {trigger}"]
    cmd_results: list[dict[str, Any]] = []
    for cmd in commands:
        proc = subprocess.run(cmd, cwd=runner.repo, capture_output=True, text=True)
        stderr_lines = "\n".join([ln for ln in proc.stderr.splitlines() if ln][:5])
        stdout = proc.stdout[:1500]
        cmd_results.append({"command": " ".join(cmd), "exit": proc.returncode, "stdout": stdout, "stderr": stderr_lines})
        lines.append(f"command: {' '.join(cmd)}")
        lines.append(f"exit: {proc.returncode}")
        if stdout:
            lines.append(f"stdout:\n{stdout}")
        if stderr_lines:
            lines.append(f"key stderr:\n{stderr_lines}")

    verification_artifacts = [r.get("command", "") for r in cmd_results if int(r.get("exit", 1)) == 0]
    verification = runner._verification_engine.verify(
        trigger,
        attributed_intentional,
        cmd_results,
        validation_artifacts=verification_artifacts,
        intended_targets=sorted(runner._current_verification_targets),
        before_contents=dict(runner._current_verification_before_contents),
    )
    finding_fingerprints = sorted(
        f"{f.category.value}|{(f.file_path or '').replace('\\', '/').lstrip('./')}|{f.message.strip().lower()}"
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
        return ""

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
                "risk": "medium" if verification.status == VerificationStatus.UNCERTAIN else "high",
                "summary": verification.summary,
            }
        )
    return "\n".join(lines)


def emit_policy_event(runner: Any, tool_name: str, tool_input: dict[str, Any], decision: Any, reason: str) -> None:
    if tool_name != "Bash":
        return
    command = str(tool_input.get("command", ""))
    cwd = str((runner.repo / str(tool_input.get("cwd", "."))).resolve())
    outcome = {"allow": "AUTO_APPROVE", "ask": "ASK", "deny": "DENY"}.get(str(decision.value if hasattr(decision, 'value') else decision).lower(), "ASK")
    ts = datetime.now(timezone.utc).isoformat()
    line = json.dumps({"timestamp": ts, "cwd": cwd, "command": command, "outcome": outcome, "reason": reason})
    log_dir = runner.repo / ".villani_code" / "logs"
    ensure_dir(log_dir)
    with (log_dir / "commands.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    runner.event_callback({"type": "command_policy", "command": command, "cwd": cwd, "outcome": outcome, "reason": reason})


def capture_edit_proposal(runner: Any, response: dict[str, Any]) -> Any:
    text_blocks = [b.get("text", "") for b in response.get("content", []) if b.get("type") == "text"]
    if not text_blocks:
        return None
    merged = "\n".join(text_blocks)
    has_diff = "--- " in merged and "+++ " in merged and "@@" in merged
    if not (runner.capture_next_diff_proposal or has_diff):
        return None
    files: list[str] = []
    for ln in merged.splitlines():
        if ln.startswith("+++ "):
            p = ln[4:].strip().split("\t")[0]
            if p.startswith("b/"):
                p = p[2:]
            files.append(p)
    proposal = runner.proposals.create(diff_text=merged, files_touched=files, summary=f"Proposed edit touching {len(files)} file(s)")
    runner.capture_next_diff_proposal = False
    return proposal


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
        plan_summary=plan.task_goal[:180],
        plan_risk=plan.risk_level.value,
        action_classes=list(plan.action_classes),
        estimated_scope=plan.estimated_scope,
        outcome_status="planned",
        checkpoint_note=f"risk={plan.risk_level.value}; scope={plan.estimated_scope}",
        current_task_summary=instruction[:220],
        last_approved_plan_summary=f"{plan.risk_level.value}:{plan.task_goal[:140]}",
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
    runner.event_callback({"type": "plan_generated", "plan": plan.to_dict(), "human": plan.to_human_text()})

    session = _build_session_state_from_plan(instruction, plan)

    if runner.skip_plan or runner.plan_policy == "off" or not plan.non_trivial:
        runner.event_callback({"type": "plan_auto_approved", "risk": plan.risk_level.value})
        update_session_state(runner.repo, session)
        return

    if runner.villani_mode:
        if plan.risk_level in {PlanRiskLevel.LOW, PlanRiskLevel.MEDIUM}:
            runner.event_callback({"type": "plan_auto_approved", "risk": plan.risk_level.value})
            update_session_state(runner.repo, session)
            return
        runner.event_callback({"type": "plan_aborted", "reason": "high risk in autonomous mode"})
        session.outcome_status = "aborted"
        update_session_state(runner.repo, session)
        raise RuntimeError("High-risk plan in autonomous mode requires explicit confirmation; aborting safely.")

    runner.event_callback({"type": "plan_approval_required", "risk": plan.risk_level.value})
    approved = runner.approval_callback("ExecutionPlan", {"summary": plan.to_human_text(), "risk": plan.risk_level.value})
    if not approved:
        runner.event_callback({"type": "plan_rejected"})
        session.outcome_status = "rejected"
        update_session_state(runner.repo, session)
        raise RuntimeError("Execution plan rejected by user.")
    runner.event_callback({"type": "plan_approved", "risk": plan.risk_level.value})
    update_session_state(runner.repo, session)


def _run_repair_attempt(runner: Any, prompt: str) -> dict[str, Any]:
    messages = build_initial_messages(runner.repo, prompt)
    system = build_system_blocks(runner.repo)
    tools = tool_specs()
    payload = {
        "model": runner.model,
        "messages": messages,
        "system": system,
        "tools": tools,
        "max_tokens": runner.max_tokens,
        "stream": False,
    }
    raw = runner.client.create_message(payload, stream=False)
    response = raw if isinstance(raw, dict) else {"content": []}
    tool_uses = [b for b in response.get("content", []) if b.get("type") == "tool_use"]
    for block in tool_uses:
        runner._execute_tool_with_policy(
            str(block.get("name", "")),
            dict(block.get("input", {})),
            str(block.get("id", "repair-tool")),
            len(messages),
        )
    text = "\n".join(
        b.get("text", "") for b in response.get("content", []) if isinstance(b, dict) and b.get("type") == "text"
    )
    return {"summary": text[:400] or "repair attempt executed"}


def run_post_execution_validation(runner: Any, changed_files: list[str]) -> str:
    if not changed_files:
        return ""
    runner.event_callback({"type": "validation_started", "changed_files": changed_files})
    result = run_validation(runner.repo, changed_files, event_callback=runner.event_callback)
    runner.event_callback(
        {
            "type": "validation_plan_selected",
            "steps": [s.step.name for s in result.plan.selected_steps],
            "scope": {
                "docs_only": result.plan.scope.docs_only,
                "config_changed": result.plan.scope.config_changed,
                "manifests_changed": result.plan.scope.manifests_changed,
            },
        }
    )

    if result.passed:
        update_session_state(
            runner.repo,
            SessionState(
                affected_files=changed_files,
                validation_summary="passed",
                outcome_status="success",
                checkpoint_note="validation_passed",
            ),
        )
        return "Validation: passed."

    attempts: list[dict[str, Any]] = []
    failing_step = result.steps[-1].step.name if result.steps else "unknown"
    failure_summary = result.failure_summary
    max_attempts = int(getattr(runner, "max_repair_attempts", 2))

    for attempt in range(1, max_attempts + 1):
        runner.event_callback({"type": "repair_attempt_started", "attempt": attempt, "failing_step": failing_step})
        prompt = (
            "Repair validation failure only.\n"
            f"Task: {getattr(getattr(runner, '_execution_plan', None), 'task_goal', '')}\n"
            f"Changed files: {', '.join(changed_files[:8])}\n"
            f"Failing step: {failing_step}\n"
            f"Failure summary:\n{failure_summary[:1200]}\n"
            f"Prior attempts: {json.dumps(attempts)[:500]}"
        )
        repair = _run_repair_attempt(runner, prompt)

        recheck = run_validation(
            runner.repo,
            changed_files,
            event_callback=runner.event_callback,
            steps_override=[failing_step],
        )
        attempts.append(
            {
                "attempt": attempt,
                "failing_step": failing_step,
                "failure_summary": failure_summary[:300],
                "repair_summary": repair.get("summary", "attempted targeted repair")[:260],
            }
        )
        if recheck.passed:
            full = run_validation(runner.repo, changed_files, event_callback=runner.event_callback)
            if full.passed:
                runner.event_callback({"type": "repair_attempt_result", "attempt": attempt, "status": "recovered"})
                update_session_state(
                    runner.repo,
                    SessionState(
                        affected_files=changed_files,
                        validation_summary="passed after repair",
                        repair_attempt_summaries=attempts,
                        repair_attempts=attempts,
                        outcome_status="recovered",
                        checkpoint_note="repair_recovered",
                    ),
                )
                return f"Validation recovered after repair attempt {attempt}."

        runner.event_callback({"type": "repair_attempt_result", "attempt": attempt, "status": "failed"})
        failing_step = recheck.steps[-1].step.name if recheck.steps else failing_step
        failure_summary = recheck.failure_summary or failure_summary

    update_session_state(
        runner.repo,
        SessionState(
            affected_files=changed_files,
            validation_summary="failed",
            last_failed_step=failing_step,
            repair_attempt_summaries=attempts,
            repair_attempts=attempts,
            outcome_status="failed",
            checkpoint_note="repair_exhausted",
        ),
    )
    return "Validation failed after bounded repair attempts. Remaining failure: " + failure_summary[:400]
