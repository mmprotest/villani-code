from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from villani_code.autonomy import VerificationStatus
from villani_code.autonomy import FindingCategory, VerificationFinding
from villani_code.indexing import DEFAULT_IGNORE, RepoIndex
from villani_code.live_display import apply_live_display_delta
from villani_code.planning import TaskMode, generate_execution_plan
from villani_code.project_memory import (
    SessionState,
    augment_validation_config_with_live_commands,
    ensure_project_memory,
    load_repo_map,
    update_session_state,
)
from villani_code.context_governance import ContextCompactor, ContextInclusionReason, ContextExclusionReason
from villani_code.validation_loop import run_validation
from villani_code.shells import baseline_import_validation_command, shell_family_for_platform
from villani_code.repair import execute_repair_loop
from villani_code.patch_apply import PatchApplyError, extract_unified_diff_targets, parse_unified_diff
from villani_code.repo_map import build_repo_map
from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path
from villani_code.retrieval import Retriever
from villani_code.mission_state import save_mission_state
from villani_code.summarizer import summarize_mission_state
from villani_code.utils import ensure_dir


_DIAGNOSIS_KEYS = ("target_file", "bug_class", "fix_intent")


def _user_message_is_safe_for_text_injection(message: dict[str, Any]) -> bool:
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return False
        return True
    return False


def _find_latest_safe_user_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if _user_message_is_safe_for_text_injection(message):
            return message
    return None


def prepend_text_to_latest_safe_user_message(messages: list[dict[str, Any]], text: str) -> bool:
    message = _find_latest_safe_user_message(messages)
    if message is None:
        return False
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = text + "\n\n" + content if content else text
        return True
    if isinstance(content, list):
        content.insert(0, {"type": "text", "text": text})
        return True
    return False


def _normalize_repo_path(value: str) -> str:
    return str(value or "").replace("\\", "/").lstrip("./")


def _single_clear_file(paths: list[str] | None) -> str | None:
    normalized = []
    for path in paths or []:
        item = _normalize_repo_path(path)
        if item:
            normalized.append(item)
    unique = sorted(set(normalized))
    if len(unique) == 1:
        return unique[0]
    return None


def _is_broad_visible_verification(command: str) -> bool:
    cmd = str(command or "").strip()
    if not cmd:
        return False
    lowered = cmd.lower()
    if "pytest" not in lowered:
        return False
    has_py_target = bool(re.search(r"(^|\s)[^\s]+\.py(::[^\s]+)?", cmd))
    has_filter = " -k " in f" {lowered} "
    return not has_py_target and not has_filter


def _extract_first_path_from_text(text: str) -> str | None:
    match = re.search(r"([\w./\\-]+\.py)", text)
    if not match:
        return None
    return _normalize_repo_path(match.group(1))


def parse_failure_signal(stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in [stdout, stderr] if part)
    lines = combined.splitlines()
    evidence: dict[str, Any] = {
        "first_failing_test": "",
        "traceback_file": "",
        "traceback_line": None,
        "error_summary": "",
        "raw_failure_excerpt": "",
    }

    test_match = re.search(r"(^|\s)([\w./-]+::[\w./\[\]-]+)", combined, re.MULTILINE)
    if test_match:
        evidence["first_failing_test"] = test_match.group(2)
    else:
        fallback = re.search(r"FAILED\s+([^\s]+)", combined)
        if fallback:
            evidence["first_failing_test"] = fallback.group(1)

    traceback = re.search(r'File "([^"]+)", line (\d+)', combined)
    if traceback:
        evidence["traceback_file"] = _normalize_repo_path(traceback.group(1))
        evidence["traceback_line"] = int(traceback.group(2))
    else:
        path = _extract_first_path_from_text(combined)
        if path:
            evidence["traceback_file"] = path

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("E   ", "AssertionError", "ValueError", "TypeError", "KeyError", "RuntimeError")):
            evidence["error_summary"] = stripped.removeprefix("E   ").strip()
            break
    if not evidence["error_summary"]:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("FAILED ") or stripped.startswith("ERROR "):
                evidence["error_summary"] = stripped
                break

    if lines:
        evidence["raw_failure_excerpt"] = "\n".join(lines[:40])
    return evidence


def _has_useful_failure_signal(evidence: dict[str, Any] | None) -> bool:
    if not evidence:
        return False
    return any(
        bool(evidence.get(key))
        for key in ["first_failing_test", "traceback_file", "traceback_line", "error_summary"]
    )


def run_pre_edit_failure_localization(runner: Any) -> dict[str, Any] | None:
    cfg = getattr(runner, "benchmark_config", None)
    visible_commands = list(getattr(cfg, "visible_verification", []) if cfg else [])
    visible_command = str(visible_commands[0]).strip() if visible_commands else ""
    expected_file = _single_clear_file(list(getattr(cfg, "expected_files", []) if cfg else []))
    plan = getattr(runner, "_execution_plan", None)
    relevant_file = _single_clear_file(list(getattr(plan, "relevant_files", []) if plan else []))
    has_traceback = bool(getattr(runner, "_pending_verification", "").strip())
    broad_visible = _is_broad_visible_verification(visible_command)
    strong_signal = bool(expected_file or relevant_file or has_traceback or not broad_visible)

    if strong_signal or not visible_command:
        runner.event_callback(
            {
                "type": "pre_edit_failure_signal_skipped",
                "reason": "strong_signal" if strong_signal else "missing_visible_verification",
                "visible_verification_command": visible_command,
            }
        )
        return None

    runner.event_callback(
        {
            "type": "pre_edit_failure_signal_attempted",
            "visible_verification_command": visible_command,
        }
    )
    try:
        with tempfile.TemporaryDirectory(prefix="villani-pre-edit-") as temp_root:
            isolated_repo = Path(temp_root) / "repo"
            shutil.copytree(runner.repo, isolated_repo)
            runner.event_callback({"type": "pre_edit_failure_signal_isolated", "isolated": True})
            proc = subprocess.run(
                ["bash", "-lc", visible_command],
                cwd=isolated_repo,
                capture_output=True,
                text=True,
            )
    except Exception as exc:  # pragma: no cover - defensive path
        runner.event_callback(
            {
                "type": "pre_edit_failure_signal_skipped",
                "reason": f"command_error:{exc.__class__.__name__}",
                "visible_verification_command": visible_command,
            }
        )
        return None

    evidence: dict[str, Any] = {
        "first_failing_test": "",
        "traceback_file": "",
        "traceback_line": None,
        "error_summary": "",
        "raw_failure_excerpt": "",
        "command": visible_command,
        "exit_code": int(proc.returncode),
    }
    if proc.returncode != 0:
        evidence.update(parse_failure_signal(proc.stdout, proc.stderr))

    runner.event_callback(
        {
            "type": "pre_edit_failure_signal_captured",
            "visible_verification_command": visible_command,
            "exit_code": int(proc.returncode),
            "failure_evidence_extracted": _has_useful_failure_signal(evidence),
            "first_failing_test": evidence.get("first_failing_test", ""),
            "traceback_file": evidence.get("traceback_file", ""),
            "error_summary": evidence.get("error_summary", ""),
        }
    )
    return evidence


def classify_diagnosis_target_confidence(
    runner: Any,
    diagnosis: dict[str, str],
    failure_evidence: dict[str, Any] | None = None,
) -> str:
    target_file = _normalize_repo_path(str(diagnosis.get("target_file", "")))
    if not target_file:
        return "weak"

    cfg = getattr(runner, "benchmark_config", None)
    expected_file = _single_clear_file(list(getattr(cfg, "expected_files", []) if cfg else []))
    if expected_file and expected_file == target_file:
        return "strong"

    if failure_evidence:
        traceback_file = _normalize_repo_path(str(failure_evidence.get("traceback_file", "")))
        if traceback_file and traceback_file == target_file:
            return "strong"
        excerpt_path = _extract_first_path_from_text(str(failure_evidence.get("raw_failure_excerpt", "")))
        if excerpt_path and excerpt_path == target_file:
            return "strong"

    plan = getattr(runner, "_execution_plan", None)
    relevant_file = _single_clear_file(list(getattr(plan, "relevant_files", []) if plan else []))
    if relevant_file and relevant_file == target_file:
        return "strong"
    return "weak"


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


def run_pre_edit_diagnosis(
    runner: Any, instruction: str, failure_evidence: dict[str, Any] | None = None
) -> dict[str, str] | None:
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
    if failure_evidence:
        if failure_evidence.get("first_failing_test"):
            evidence_lines.append("First failing test: " + str(failure_evidence["first_failing_test"]))
        if failure_evidence.get("traceback_file"):
            file_line = str(failure_evidence["traceback_file"])
            if failure_evidence.get("traceback_line"):
                file_line += f":{failure_evidence['traceback_line']}"
            evidence_lines.append("Traceback location: " + file_line)
        if failure_evidence.get("error_summary"):
            evidence_lines.append("Error summary: " + str(failure_evidence["error_summary"]))
        if failure_evidence.get("raw_failure_excerpt"):
            excerpt = str(failure_evidence["raw_failure_excerpt"]).strip()
            if excerpt:
                evidence_lines.append("Raw failure excerpt:\n" + excerpt[:1200])

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
    prepend_text_to_latest_safe_user_message(messages, hint)


def prepare_messages_for_model(runner: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = deepcopy(messages)
    _inject_shell_reminder(runner, prepared)
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


def _inject_shell_reminder(runner: Any, messages: list[dict[str, Any]]) -> None:
    if not messages:
        return
    shell_env = dict(getattr(runner, "_shell_environment", {}) or {})
    shell_family = str(shell_env.get("shell_family", "")).strip().lower()
    if shell_family == "cmd":
        reminder = "Shell: Windows cmd. Do not use Unix tools like head, grep, tail, rm, or heredocs."
    elif shell_family == "powershell":
        reminder = "Shell: Windows PowerShell. Avoid bash heredocs and bash-only syntax."
    elif shell_family in {"bash", "zsh"}:
        reminder = f"Shell: {shell_family}. Use POSIX shell syntax."
    else:
        return
    last = messages[-1]
    if last.get("role") != "user":
        return
    content = last.get("content", [])
    if not isinstance(content, list):
        return
    if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return
    if any(isinstance(block, dict) and block.get("type") == "text" and str(block.get("text", "")).startswith("Shell: ") for block in content):
        return
    content.insert(0, {"type": "text", "text": reminder})


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


def _recovery_blocked_feedback(reason: str, tool: str, path: str, detail: str = "") -> str:
    payload = {
        "recovery_blocked": True,
        "reason": reason,
        "tool": tool,
        "path": path,
    }
    if detail:
        payload["detail"] = detail
    return json.dumps(payload, sort_keys=True)


def _recovery_target_switch_blocked_feedback(primary_target: str, attempted_target: str, *, reason: str = "recovery_target_switch_blocked") -> str:
    payload = {
        "classification": "blocked",
        "short_reason": reason,
        "primary_execution_target": primary_target,
        "attempted_target": attempted_target,
        "recovery_blocked": True,
    }
    return json.dumps(payload, sort_keys=True)


def _patch_deletes_target_file(unified_diff: str, failing_file: str) -> bool:
    try:
        parsed = parse_unified_diff(unified_diff)
    except PatchApplyError:
        return False
    for file_patch in parsed:
        old_path = str(file_patch.old_path).replace("\\", "/").lstrip("./")
        new_path = str(file_patch.new_path).replace("\\", "/").lstrip("./")
        if old_path == failing_file and file_patch.new_path == "/dev/null":
            return True
        if new_path == failing_file and file_patch.old_path == "/dev/null":
            continue
    return False


def _command_has_masking_patterns(command: str) -> bool:
    cmd = str(command or "").strip().lower()
    if not cmd:
        return False
    if "||" in cmd:
        return True
    if "&& echo" in cmd:
        return True
    if "2>&1 |" in cmd:
        return True
    if "timeout" in cmd and "echo" in cmd:
        return True
    return False


def _is_validation_or_launch_command(command: str) -> bool:
    lowered = str(command or "").strip().lower()
    if not lowered:
        return False
    markers = ("pytest", "unittest", "test", "validate", "verification", "python ", "python3 ", "uv run", "poetry run")
    return any(marker in lowered for marker in markers)


def _extract_command_python_target(command: str) -> str:
    matches = re.findall(r"([\w./-]+\.py)\b", str(command or ""))
    for match in matches:
        normalized = _normalize_repo_path(match)
        if normalized and not normalized.startswith("tests/"):
            return normalized
    return ""


def _primary_execution_target(runner: Any) -> str:
    return str(getattr(runner, "_primary_execution_target", "")).replace("\\", "/").lstrip("./")


_TARGET_EVIDENCE_ORDER = {"none": 0, "write_only": 1, "indirect_validation": 2, "direct_validation": 3, "direct_run": 4}


def _normalize_contract_cwd(runner: Any, cwd: str | None) -> str:
    raw = str(cwd or "").strip()
    if not raw:
        return "."
    try:
        repo_root = Path(getattr(runner, "repo", ".")).resolve()
        resolved = Path(raw).resolve()
        return str(resolved.relative_to(repo_root)).replace("\\", "/").lstrip("./") or "."
    except Exception:
        return _normalize_repo_path(raw) or "."


def _target_contract(runner: Any, target: str, cwd: str | None = None) -> dict[str, str]:
    return {"target": _normalize_repo_path(target), "cwd": _normalize_contract_cwd(runner, cwd)}


def _primary_execution_contract(runner: Any) -> dict[str, str]:
    return {
        "target": _primary_execution_target(runner),
        "cwd": _normalize_contract_cwd(runner, str(getattr(runner, "_primary_execution_target_cwd", "") or ".")),
    }


def _same_target_contract(a: dict[str, str], b: dict[str, str]) -> bool:
    return str(a.get("target", "")) == str(b.get("target", "")) and str(a.get("cwd", ".")) == str(b.get("cwd", "."))


def _target_contract_summary(contract: dict[str, str]) -> str:
    return f"{contract.get('target', '') or 'none'} @ {contract.get('cwd', '.') or '.'}"


def _sync_recovery_state_to_mission(runner: Any) -> None:
    mission_state = getattr(runner, "_mission_state", None)
    if mission_state is None:
        return
    mission_state.recovery_mode = bool(getattr(runner, "_recovery_mode", False))
    mission_state.primary_execution_target = _primary_execution_target(runner)
    mission_state.primary_execution_cwd = str(getattr(runner, "_primary_execution_target_cwd", "") or ".")
    mission_state.primary_execution_evidence = str(getattr(runner, "_primary_execution_target_evidence", "none"))
    mission_state.primary_target_minimally_valid = bool(getattr(runner, "_primary_target_minimally_valid", False))
    save_mission_state(runner.repo, mission_state)


def _snapshot_repo_files(repo: Path) -> set[str]:
    collected: set[str] = set()
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo)).replace("\\", "/").lstrip("./")
        if rel.startswith(".git/") or rel.startswith(".villani_code/"):
            continue
        collected.add(rel)
    return collected


def _seed_primary_execution_target(runner: Any, target: str, *, cwd: str | None = None, evidence: str = "write_only") -> str:
    contract = _target_contract(runner, target, cwd)
    normalized = contract["target"]
    if not normalized:
        return _primary_execution_target(runner)
    current = _primary_execution_contract(runner)
    current_evidence = str(getattr(runner, "_primary_execution_target_evidence", "none"))
    current_strength = _TARGET_EVIDENCE_ORDER.get(current_evidence, 0)
    incoming_strength = _TARGET_EVIDENCE_ORDER.get(str(evidence or "none"), 0)
    if current["target"]:
        if incoming_strength < current_strength:
            return current["target"]
        if incoming_strength == current_strength and not _same_target_contract(current, contract):
            return current["target"]
    runner._primary_execution_target = normalized
    runner._primary_execution_target_cwd = contract["cwd"]
    runner._primary_execution_target_evidence = str(evidence or "none")
    runner._primary_target_minimally_valid = bool(getattr(runner, "_active_solution_last_validation_ok", False))
    _sync_recovery_state_to_mission(runner)
    return normalized


def _detect_hard_failure(cmd_results: list[dict[str, Any]]) -> dict[str, str] | None:
    for result in cmd_results:
        command = str(result.get("command", "")).strip()
        exit_code = int(result.get("exit", 0))
        stdout = str(result.get("stdout", "") or "")
        stderr = str(result.get("stderr", "") or "")
        combined = f"{stdout}\n{stderr}".strip()
        lower = combined.lower()
        has_crash_signal = any(
            token in combined
            for token in [
                "SyntaxError",
                "NameError",
                "ImportError",
                "ModuleNotFoundError",
            ]
        ) or ("traceback (most recent call last)" in lower) or ("unhandled exception" in lower)
        if exit_code != 0 or has_crash_signal:
            evidence = parse_failure_signal(stdout, stderr)
            summary = str(evidence.get("error_summary", "")).strip()
            if not summary:
                summary = (stderr or stdout or f"Command failed with exit {exit_code}").splitlines()[0][:200]
            failing_file = str(evidence.get("traceback_file", "")).strip()
            return {
                "failing_command": command[:200],
                "failing_file": failing_file,
                "error_summary": summary[:220],
            }
    return None


def _command_targets_active_solution(command: str, active_solution_file: str, validation_targets: set[str]) -> bool:
    active = str(active_solution_file or "").replace("\\", "/").lstrip("./")
    if not active:
        return False
    if not _is_validation_or_launch_command(command):
        return False
    normalized_targets = {str(t).replace("\\", "/").lstrip("./") for t in validation_targets if str(t).strip()}
    explicit_target = _extract_command_python_target(command)
    if explicit_target:
        return explicit_target == active
    cmd = str(command or "")
    if active in cmd:
        return True
    return len(normalized_targets) == 1 and active in normalized_targets


def _suggest_live_validation_commands(targets: set[str], observed_commands: list[str]) -> list[str]:
    suggestions: list[str] = []
    seen: set[str] = set()
    for command in observed_commands:
        trimmed = str(command or "").strip()
        if not trimmed or trimmed in seen:
            continue
        if _is_validation_or_launch_command(trimmed):
            seen.add(trimmed)
            suggestions.append(trimmed)
    for target in sorted(targets):
        normalized = _normalize_repo_path(target)
        if not normalized:
            continue
        if normalized.endswith(".py"):
            candidate = f"python {normalized}"
            if candidate not in seen:
                seen.add(candidate)
                suggestions.append(candidate)
    return suggestions[:8]


def _has_hard_failure_signal(stdout: str, stderr: str, exit_code: int) -> bool:
    combined = f"{stdout}\n{stderr}".strip()
    lower = combined.lower()
    return (
        exit_code != 0
        or any(token in combined for token in ("SyntaxError", "NameError", "ImportError", "ModuleNotFoundError"))
        or ("traceback (most recent call last)" in lower)
        or ("unhandled exception" in lower)
    )


def activate_live_recovery_on_primary_failure(
    runner: Any,
    *,
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    attempted_target: str = "",
    attempted_cwd: str | None = None,
) -> bool:
    attempted_contract = _target_contract(runner, attempted_target, attempted_cwd)
    primary_contract = _primary_execution_contract(runner)
    if not attempted_contract["target"] or not primary_contract["target"] or not _same_target_contract(attempted_contract, primary_contract):
        return False
    if not _is_validation_or_launch_command(command):
        return False
    if not _has_hard_failure_signal(stdout, stderr, exit_code):
        return False
    evidence = parse_failure_signal(stdout, stderr)
    summary = str(evidence.get("error_summary", "")).strip()
    if not summary:
        summary = (stderr or stdout or f"Command failed with exit {exit_code}").splitlines()[0][:220]
    traceback_file = _normalize_repo_path(str(evidence.get("traceback_file", "")).strip())
    if traceback_file and (traceback_file == primary_contract["target"] or traceback_file.endswith(f"/{primary_contract['target']}")):
        failing_file = primary_contract["target"]
    else:
        failing_file = primary_contract["target"]
    runner._recovery_mode = True
    runner._recovery_target_switch_blocked = False
    runner._primary_target_minimally_valid = False
    runner._failing_file = failing_file
    runner._failing_target_contract_summary = _target_contract_summary(primary_contract)
    runner._failing_error_summary = summary[:220]
    runner._failing_command = str(command or "")[:200]
    runner._file_was_read_since_failure = False
    runner._recovery_files_at_failure = _snapshot_repo_files(runner.repo)
    runner._recovery_created_artifacts = set()
    runner._active_solution_file = primary_contract["target"]
    runner._active_solution_last_validation_ok = False
    runner._active_solution_last_validation_summary = summary[:220]
    runner.event_callback(
        {
            "type": "recovery_mode_activated",
            "failing_file": failing_file,
            "active_solution_file": primary_contract["target"],
            "primary_execution_target": primary_contract["target"],
            "primary_execution_cwd": primary_contract["cwd"],
            "failing_error_summary": summary[:220],
            "failing_command": runner._failing_command,
        }
    )
    _sync_recovery_state_to_mission(runner)
    return True


def small_model_tool_guard(runner: Any, tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name in {"Write", "Patch"} and bool(getattr(runner, "_recovery_mode", False)):
        failing_file = str(getattr(runner, "_failing_file", "")).replace("\\", "/").lstrip("./")
        active_solution_file = str(getattr(runner, "_active_solution_file", "")).replace("\\", "/").lstrip("./")
        locked_file = active_solution_file or failing_file
        if locked_file:
            targets: set[str] = set()
            if tool_name == "Write":
                target = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
                if target:
                    targets.add(target)
            else:
                diff_text = str(tool_input.get("unified_diff", ""))
                default_path = str(tool_input.get("file_path", "") or "") or None
                try:
                    targets = {
                        str(path).replace("\\", "/").lstrip("./")
                        for path in extract_unified_diff_targets(diff_text, default_file_path=default_path)
                    }
                except PatchApplyError:
                    if default_path:
                        targets = {str(default_path).replace("\\", "/").lstrip("./")}
            if locked_file in targets and not bool(getattr(runner, "_file_was_read_since_failure", False)):
                return _recovery_blocked_feedback(
                    "read_required_before_edit",
                    tool_name,
                    locked_file,
                    "read the failing file before editing",
                )
            if tool_name == "Write" and locked_file in targets:
                content = str(tool_input.get("content", ""))
                active_is_failed = (
                    locked_file == active_solution_file
                    and bool(getattr(runner, "_recovery_mode", False))
                    and getattr(runner, "_active_solution_last_validation_ok", None) is False
                )
                if active_is_failed and content.strip():
                    return _recovery_blocked_feedback(
                        "full_write_blocked_for_active_solution",
                        tool_name,
                        locked_file,
                        "use a bounded patch/edit while recovering from hard failure",
                    )
                existing_path = (runner.repo / locked_file).resolve()
                before_text = existing_path.read_text(encoding="utf-8", errors="replace") if existing_path.exists() and existing_path.is_file() else ""
                if not content.strip():
                    return _recovery_blocked_feedback("delete_blocked_for_failing_file", tool_name, locked_file)
                before_lines = before_text.splitlines()
                after_lines = content.splitlines()
                changed_lines = sum(1 for a, b in zip(before_lines, after_lines) if a != b) + abs(len(before_lines) - len(after_lines))
                if before_lines and changed_lines > max(120, int(len(before_lines) * 0.8)):
                    return _recovery_blocked_feedback("rewrite_blocked_for_failing_file", tool_name, locked_file)
            if tool_name == "Patch" and locked_file in targets:
                diff_text = str(tool_input.get("unified_diff", ""))
                if _patch_deletes_target_file(diff_text, locked_file):
                    return _recovery_blocked_feedback("delete_blocked_for_failing_file", tool_name, locked_file)
    if tool_name == "Bash":
        command = str(tool_input.get("command", "")).strip()
        if _is_validation_or_launch_command(command) and _command_has_masking_patterns(command):
            return _recovery_blocked_feedback("suspicious_validation_command", tool_name, "", "masked failure patterns detected")
        target = _extract_command_python_target(command)
        attempted_contract = _target_contract(runner, target, tool_input.get("cwd"))
        active_solution_file = str(getattr(runner, "_active_solution_file", "")).replace("\\", "/").lstrip("./")
        primary_contract = _primary_execution_contract(runner)
        primary_target = primary_contract["target"]
        if _is_validation_or_launch_command(command) and target and not active_solution_file:
            runner._active_solution_file = target
        if _is_validation_or_launch_command(command) and target:
            _seed_primary_execution_target(runner, target, cwd=tool_input.get("cwd"), evidence="direct_run")
            refresh_live_validation_candidates(runner, [target], observed_commands=[command])
        if (
            bool(getattr(runner, "_recovery_mode", False))
            and _is_validation_or_launch_command(command)
            and target
            and primary_target
            and not _same_target_contract(attempted_contract, primary_contract)
            and not bool(getattr(runner, "_primary_target_minimally_valid", False))
        ):
            runner._recovery_target_switch_blocked = True
            recovery_created_artifacts = set(getattr(runner, "_recovery_created_artifacts", set()))
            recovery_files_at_failure = set(getattr(runner, "_recovery_files_at_failure", set()))
            confidently_new_target = target in recovery_created_artifacts or (
                bool(recovery_files_at_failure) and target not in recovery_files_at_failure
            )
            if confidently_new_target:
                return _recovery_target_switch_blocked_feedback(primary_target, target, reason="recovery_new_wrapper_target_blocked")
            return _recovery_target_switch_blocked_feedback(primary_target, target)

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
                from villani_code import state_tooling

                guard_turn_index = (
                    runner._current_turn_index if isinstance(getattr(runner, "_current_turn_index", None), int) else 0
                )
                guard_tool_use_id = f"guard-read-{guard_turn_index}-{fp.replace('/', '_')}"
                read_result = state_tooling.execute_tool_with_lifecycle(
                    runner=runner,
                    tool_name="Read",
                    tool_input={"file_path": fp, "max_bytes": 8000},
                    tool_use_id=guard_tool_use_id,
                    forced=True,
                    turn_index=guard_turn_index,
                )
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




def _collect_changed_python_files(runner: Any) -> list[str]:
    current_changed = set(git_changed_files(runner.repo))
    attributed_changed = sorted(current_changed - runner._verification_baseline_changed)
    return [
        path
        for path in attributed_changed
        if path.endswith(".py") and not is_ignored_repo_path(path)
    ]


def _is_pytest_based_verification(runner: Any) -> bool:
    cfg = getattr(runner, "benchmark_config", None)
    visible = list(getattr(cfg, "visible_verification", []) if cfg else [])
    if any("pytest" in str(cmd).lower() for cmd in visible):
        return True
    plan = getattr(runner, "_execution_plan", None)
    steps = list(getattr(plan, "validation_steps", []) if plan else [])
    return any("pytest" in str(step).lower() for step in steps)


def _run_patch_sanity_check(runner: Any) -> dict[str, Any]:
    checked_files = _collect_changed_python_files(runner)
    telemetry = {
        "first_attempt_write_lock_active": bool(getattr(runner, "_first_attempt_write_lock_active", False)),
        "locked_target_file": str(getattr(runner, "_first_attempt_locked_target", "") or ""),
        "syntax_sanity_ran": bool(checked_files),
        "collection_sanity_ran": False,
        "collection_sanity_passed": None,
    }
    if not checked_files:
        runner.event_callback(
            {
                "type": "patch_sanity_check_skipped",
                "reason": "no_relevant_changed_python_files",
                **telemetry,
            }
        )
        return {
            "ran": False,
            "checked_files": [],
            "passed": True,
            "failure_class": "",
            "reason": "no_relevant_changed_python_files",
            "stdout": "",
            "stderr": "",
            "command": "",
            **telemetry,
        }

    cmd = [sys.executable, "-m", "py_compile", *checked_files]
    proc = subprocess.run(cmd, cwd=runner.repo, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        reason = stderr.splitlines()[0] if stderr else "python compile sanity failed"
        runner.event_callback(
            {
                "type": "patch_sanity_check_failed",
                "checked_files": checked_files,
                "failure_class": "patch_sanity_failed",
                "reason": reason,
                "command": " ".join(cmd),
                **telemetry,
            }
        )
        runner.event_callback(
            {
                "type": "failure_classified",
                "category": "patch_sanity_failed",
                "summary": reason,
                "next_strategy": "Fix syntax/import structure in edited file(s) and retry once.",
                "occurrence": 1,
                "failed_files": checked_files,
            }
        )
        return {
            "ran": True,
            "checked_files": checked_files,
            "passed": False,
            "failure_class": "patch_sanity_failed",
            "reason": reason,
            "stdout": stdout,
            "stderr": stderr,
            "command": " ".join(cmd),
            **telemetry,
        }

    runner.event_callback(
        {
            "type": "patch_sanity_check_passed",
            "checked_files": checked_files,
            "command": " ".join(cmd),
            **telemetry,
        }
    )
    if not _is_pytest_based_verification(runner):
        return {
            "ran": True,
            "checked_files": checked_files,
            "passed": True,
            "failure_class": "",
            "reason": "",
            "stdout": stdout,
            "stderr": stderr,
            "command": " ".join(cmd),
            **telemetry,
        }

    collect_cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    collect_proc = subprocess.run(collect_cmd, cwd=runner.repo, capture_output=True, text=True)
    collect_stdout = collect_proc.stdout.strip()
    collect_stderr = collect_proc.stderr.strip()
    telemetry["collection_sanity_ran"] = True
    telemetry["collection_sanity_passed"] = collect_proc.returncode == 0
    if collect_proc.returncode != 0:
        reason = (
            collect_stderr.splitlines()[0]
            if collect_stderr
            else (collect_stdout.splitlines()[0] if collect_stdout else "pytest collection sanity failed")
        )
        runner.event_callback(
            {
                "type": "collection_sanity_check_failed",
                "failure_class": "collection_sanity_failed",
                "checked_files": checked_files,
                "command": " ".join(collect_cmd),
                "exit_code": int(collect_proc.returncode),
                "stdout_excerpt": collect_stdout[:500],
                "stderr_excerpt": collect_stderr[:500],
                "reason": reason,
                **telemetry,
            }
        )
        runner.event_callback(
            {
                "type": "failure_classified",
                "category": "collection_sanity_failed",
                "summary": reason,
                "next_strategy": "Fix import/test collection structure in edited file(s) and retry once.",
                "occurrence": 1,
                "failed_files": checked_files,
            }
        )
        return {
            "ran": True,
            "checked_files": checked_files,
            "passed": False,
            "failure_class": "collection_sanity_failed",
            "reason": reason,
            "stdout": collect_stdout,
            "stderr": collect_stderr,
            "command": " ".join(collect_cmd),
            "exit_code": int(collect_proc.returncode),
            **telemetry,
        }

    runner.event_callback(
        {
            "type": "collection_sanity_check_passed",
            "checked_files": checked_files,
            "command": " ".join(collect_cmd),
            "exit_code": int(collect_proc.returncode),
            **telemetry,
        }
    )
    return {
        "ran": True,
        "checked_files": checked_files,
        "passed": True,
        "failure_class": "",
        "reason": "",
        "stdout": collect_stdout,
        "stderr": collect_stderr,
        "command": " ".join(collect_cmd),
        "exit_code": int(collect_proc.returncode),
        **telemetry,
    }


def _compact_patch_sanity_retry_hint(sanity: dict[str, Any]) -> str:
    files = sanity.get("checked_files", [])
    target = files[0] if files else "the edited file"
    if sanity.get("failure_class") == "collection_sanity_failed":
        return (
            "The previous edit broke import/test collection. "
            f"Fix structure in {target} while preserving intended behavior."
        )
    return (
        "Patch sanity gate failed (syntax/import structure). "
        f"Fix structure in {target} while preserving intended behavior."
    )


def _build_compact_validation_summary(
    runner: Any,
    *,
    target: str,
    summary: str,
    repeated_without_new_evidence: bool,
    artifact_signature: str,
) -> str:
    runner._last_validation_target = target
    runner._last_validation_summary = summary
    runner._validation_repeated_without_new_evidence = repeated_without_new_evidence
    runner._last_validation_artifact_signature = artifact_signature
    emitted_fingerprint = json.dumps(
        {
            "last_validation_target": target,
            "last_validation_summary": summary,
            "validation_repeated_without_new_evidence": bool(repeated_without_new_evidence),
            "last_validation_artifact_signature": artifact_signature,
        },
        sort_keys=True,
    )
    if emitted_fingerprint == getattr(runner, "_last_emitted_validation_fingerprint", ""):
        return ""
    runner._last_emitted_validation_fingerprint = emitted_fingerprint
    return (
        "<validation_summary>\n"
        f"last_validation_target: {target or 'none'}\n"
        f"last_validation_summary: {summary or 'none'}\n"
        "validation_repeated_without_new_evidence: "
        f"{str(bool(repeated_without_new_evidence)).lower()}\n"
        "</validation_summary>"
    )


def run_post_edit_verification(runner: Any, trigger: str = "edit") -> str:
    had_pending_retry = bool(getattr(runner, "_patch_sanity_retry_pending", False))
    sanity = _run_patch_sanity_check(runner)
    if not sanity.get("ran") or sanity.get("passed"):
        if had_pending_retry:
            runner.event_callback(
                {
                    "type": "patch_sanity_retry_attempted",
                    "checked_files": sanity.get("checked_files", []),
                    "retry_attempted": True,
                    "retry_resolved": True,
                    "retry_reason": "sanity_failure",
                }
            )
        runner._patch_sanity_retry_pending = False
        verification = run_verification(runner, trigger)
        runner._first_attempt_write_lock_active = False
        return verification

    failed_files = sanity.get("checked_files", [])
    if not runner._patch_sanity_retry_pending:
        runner._patch_sanity_retry_pending = True
        hint = _compact_patch_sanity_retry_hint(sanity)
        runner.event_callback(
            {
                "type": "patch_sanity_retry_attempted",
                "checked_files": failed_files,
                "retry_attempted": True,
                "retry_resolved": False,
                "retry_reason": "sanity_failure",
            }
        )
        if sanity.get("failure_class") == "collection_sanity_failed":
            runner.event_callback(
                {
                    "type": "collection_sanity_retry_attempted",
                    "checked_files": failed_files,
                    "retry_attempted": True,
                }
            )
        return (
            "<verification>\n"
            f"trigger: {trigger}\n"
            "patch_sanity_gate: failed\n"
            f"failure_class: {sanity.get('failure_class', 'patch_sanity_failed')}\n"
            f"checked_files: {json.dumps(failed_files)}\n"
            f"command: {sanity.get('command', '')}\n"
            f"reason: {sanity.get('reason', '')}\n"
            f"next: {hint}\n"
            "</verification>"
        )

    runner._patch_sanity_retry_pending = False
    runner.event_callback(
        {
            "type": "patch_sanity_retry_attempted",
            "checked_files": failed_files,
            "retry_attempted": True,
            "retry_resolved": False,
            "final": True,
            "retry_reason": "sanity_failure",
        }
    )
    verification = run_verification(runner, f"{trigger} (after_sanity_retry_failed)")
    runner._first_attempt_write_lock_active = False
    return verification

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
    if attributed_intentional:
        refresh_live_validation_candidates(runner, attributed_intentional)
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
        r.get("command", "")
        for r in cmd_results
        if int(r.get("exit", 1)) == 0 and not _command_has_masking_patterns(str(r.get("command", "")))
    ]
    if verification_artifacts:
        live_targets = sorted(set(getattr(runner, "_current_verification_targets", set())) or set(attributed_intentional))
        refresh_live_validation_candidates(
            runner,
            live_targets,
            observed_commands=[str(command) for command in verification_artifacts if str(command).strip()],
        )
    validation_target_paths = sorted(set(runner._current_verification_targets) or set(attributed_intentional))
    validation_target = json.dumps(validation_target_paths)
    artifact_signature = json.dumps(sorted(verification_artifacts))
    repeated_without_new_evidence = (
        validation_target == getattr(runner, "_last_validation_target", "")
        and artifact_signature == getattr(runner, "_last_validation_artifact_signature", "")
    )
    verification = runner._verification_engine.verify(
        trigger,
        attributed_intentional,
        cmd_results,
        validation_artifacts=verification_artifacts,
        intended_targets=sorted(runner._current_verification_targets),
        before_contents=dict(runner._current_verification_before_contents),
    )
    active_solution_file = str(getattr(runner, "_active_solution_file", "")).replace("\\", "/").lstrip("./")
    direct_target = ""
    for result in cmd_results:
        rendered_command = str(result.get("command", ""))
        if not _is_validation_or_launch_command(rendered_command):
            continue
        seeded_target = _extract_command_python_target(rendered_command)
        if seeded_target:
            direct_target = seeded_target
            break
    if direct_target and not active_solution_file:
        runner._active_solution_file = direct_target
        active_solution_file = direct_target
    if not active_solution_file and not direct_target:
        fallback_target = _single_clear_file(validation_target_paths)
        if fallback_target:
            active_solution_file = fallback_target
            runner._active_solution_file = fallback_target
    evidence_kind = "direct_run" if direct_target else "indirect_validation"
    primary_target = _seed_primary_execution_target(runner, active_solution_file or direct_target, cwd=str(runner.repo), evidence=evidence_kind)
    validation_targets = set(validation_target_paths)
    active_cmd_results = [
        result
        for result in cmd_results
        if _command_targets_active_solution(
            str(result.get("command", "")),
            active_solution_file,
            validation_targets,
        )
    ]
    if active_cmd_results:
        latest = active_cmd_results[-1]
        exit_code = int(latest.get("exit", 0))
        stdout = str(latest.get("stdout", "") or "")
        stderr = str(latest.get("stderr", "") or "")
        has_failure = _has_hard_failure_signal(stdout, stderr, exit_code)
        if has_failure:
            runner._active_solution_last_validation_ok = False
            evidence_lines = [ln.strip() for ln in (stderr or stdout or f"exit={exit_code}").splitlines() if ln.strip()]
            summary_line = (evidence_lines[-1] if evidence_lines else f"exit={exit_code}")[:220]
            runner._active_solution_last_validation_summary = summary_line
            verification.status = VerificationStatus.FAIL
        else:
            runner._active_solution_last_validation_ok = True
            runner._active_solution_last_validation_summary = "validation passed"
            if primary_target and active_solution_file and primary_target == active_solution_file:
                runner._primary_target_minimally_valid = True
                _sync_recovery_state_to_mission(runner)

    hard_failure = _detect_hard_failure(cmd_results)
    if hard_failure:
        summary = hard_failure.get("error_summary", "") or "runtime/validation command failed"
        failing_file = (
            hard_failure.get("failing_file", "")
            or _single_clear_file(attributed_intentional)
            or _single_clear_file(sorted(runner._current_verification_targets))
            or ""
        )
        failing_file = _normalize_repo_path(failing_file)
        if active_solution_file and failing_file == active_solution_file:
            failing_file = active_solution_file
        elif not active_solution_file and failing_file:
            runner._active_solution_file = failing_file
            active_solution_file = failing_file
        primary_target = _seed_primary_execution_target(
            runner, active_solution_file or failing_file, cwd=str(runner.repo), evidence="direct_validation"
        )
        runner._recovery_mode = True
        runner._recovery_target_switch_blocked = False
        runner._primary_target_minimally_valid = False
        runner._failing_file = failing_file
        runner._failing_target_contract_summary = _target_contract_summary(_primary_execution_contract(runner))
        runner._failing_error_summary = summary
        runner._failing_command = hard_failure.get("failing_command", "")
        runner._file_was_read_since_failure = False
        runner._recovery_files_at_failure = _snapshot_repo_files(runner.repo)
        runner._recovery_created_artifacts = set()
        if active_solution_file and failing_file == active_solution_file:
            runner._active_solution_last_validation_ok = False
            runner._active_solution_last_validation_summary = summary[:220]
        verification.status = VerificationStatus.FAIL
        verification.confidence_score = min(float(getattr(verification, "confidence_score", 0.5)), 0.05)
        verification.summary = f"Hard failure detected: {summary}"
        verification.findings.append(
            VerificationFinding(
                category=FindingCategory.REGRESSION,
                message=f"Hard failure: {summary}",
                file_path=failing_file or None,
                severity="high",
            )
        )
        runner.event_callback(
            {
                "type": "recovery_mode_activated",
                "failing_file": failing_file,
                "active_solution_file": str(getattr(runner, "_active_solution_file", "")),
                "primary_execution_target": primary_target,
                "primary_execution_cwd": str(getattr(runner, "_primary_execution_target_cwd", "") or "."),
                "failing_error_summary": summary,
                "failing_command": runner._failing_command,
            }
        )
        _sync_recovery_state_to_mission(runner)
    elif bool(getattr(runner, "_recovery_mode", False)) and verification.status in {VerificationStatus.PASS, VerificationStatus.REPAIRED}:
        primary_target = _primary_execution_target(runner)
        validated_primary = False
        if primary_target:
            targeted_results = [
                result
                for result in cmd_results
                if _command_targets_active_solution(
                    str(result.get("command", "")),
                    primary_target,
                    validation_targets,
                )
            ]
            validated_primary = bool(targeted_results) and all(
                not _has_hard_failure_signal(
                    str(result.get("stdout", "") or ""),
                    str(result.get("stderr", "") or ""),
                    int(result.get("exit", 0)),
                )
                for result in targeted_results
            )
        if validated_primary:
            runner._primary_target_minimally_valid = True
            runner._recovery_mode = False
            runner._recovery_target_switch_blocked = False
            runner._failing_file = ""
            runner._failing_target_contract_summary = ""
            runner._failing_error_summary = ""
            runner._failing_command = ""
            runner._file_was_read_since_failure = False
            runner._recovery_files_at_failure = set()
            runner._recovery_created_artifacts = set()
            runner.event_callback({"type": "recovery_mode_cleared"})
            _sync_recovery_state_to_mission(runner)
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
        summary = "status repeated without new validation evidence"
        lines.append("verification state repeated")
        lines.append("no new evidence was produced")
        lines.append("next step must either change target, change validation evidence, or stop")
        lines.append("</verification>")
        runner.event_callback(
            {
                "type": "verification_detail",
                "trigger": trigger,
                "target": validation_target_paths,
                "detail": "\n".join(lines),
                "repeated_without_new_evidence": repeated_without_new_evidence,
            }
        )
        return _build_compact_validation_summary(
            runner,
            target=validation_target,
            summary=summary,
            repeated_without_new_evidence=repeated_without_new_evidence,
            artifact_signature=artifact_signature,
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
    summary = f"status={verification.status.value}; confidence={verification.confidence_score}"
    if verification.findings:
        summary += f"; findings={len(verification.findings)}"
    runner.event_callback(
        {
            "type": "verification_detail",
            "trigger": trigger,
            "target": validation_target_paths,
            "detail": "\n".join(lines),
            "repeated_without_new_evidence": repeated_without_new_evidence,
        }
    )
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
    return _build_compact_validation_summary(
        runner,
        target=validation_target,
        summary=summary,
        repeated_without_new_evidence=repeated_without_new_evidence,
        artifact_signature=artifact_signature,
    )


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
    if getattr(runner, "_mission_state", None) is not None and getattr(runner, "_mission_dir", None) is not None:
        mission_dir = runner._mission_dir
        ensure_dir(mission_dir)
        (mission_dir / "messages.json").write_text(json.dumps(messages, indent=2), encoding="utf-8")
        summary_text = summarize_mission_state(runner._mission_state)
        runner._mission_state.compact_summary = summary_text
        (mission_dir / "working_summary.md").write_text(summary_text + "\n", encoding="utf-8")
        save_mission_state(runner.repo, runner._mission_state)
        if getattr(runner, "_event_recorder", None) is not None:
            runner._event_recorder.write_digest()
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


def _with_shell_environment(runner: Any, session: SessionState) -> SessionState:
    shell_env = dict(getattr(runner, "_shell_environment", {}) or {})
    session.shell_environment = {str(k): str(v) for k, v in shell_env.items() if str(k).strip()}
    return session


def ensure_project_memory_and_plan(runner: Any, instruction: str) -> None:
    if getattr(runner, "_planning_read_only", False):
        return
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
        update_session_state(runner.repo, _with_shell_environment(runner, session))
        return

    if runner.villani_mode:
        runner.event_callback({"type": "plan_auto_approved", "risk": plan.risk_level.value})
        update_session_state(runner.repo, _with_shell_environment(runner, session))
        return

    runner.event_callback({"type": "plan_approval_required", "risk": plan.risk_level.value})
    approved = runner.approval_callback("ExecutionPlan", {"summary": plan.to_human_text(), "risk": plan.risk_level.value})
    if not approved:
        runner.event_callback({"type": "plan_rejected"})
        session.outcome_status = "rejected"
        session.next_step_hints = ["Revise plan scope or lower risk before retrying"]
        update_session_state(runner.repo, _with_shell_environment(runner, session))
        raise RuntimeError("Execution plan rejected by user.")
    runner.event_callback({"type": "plan_approved", "risk": plan.risk_level.value})
    update_session_state(runner.repo, _with_shell_environment(runner, session))




def run_post_execution_validation(runner: Any, changed_files: list[str]) -> str:
    if getattr(runner, "_planning_read_only", False):
        return ""
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
        update_session_state(runner.repo, _with_shell_environment(runner, SessionState(
            affected_files=changed_files,
            validation_plan_summary=[s.step.name for s in result.plan.selected_steps],
            validation_summary="passed",
            outcome_status="success",
            next_step_hints=["Finalize and report output"],
            handoff_checkpoint="validation_passed",
        )))
        if getattr(runner, "_mission_state", None) is not None:
            runner._mission_state.validation_failures = []
            runner._mission_state.last_checkpoint_id = checkpoint.checkpoint_id
            save_mission_state(runner.repo, runner._mission_state)
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
        update_session_state(runner.repo, _with_shell_environment(runner, SessionState(
            affected_files=changed_files,
            validation_plan_summary=[s.step.name for s in result.plan.selected_steps],
            validation_summary="passed after repair",
            repair_attempt_summaries=[asdict(a) for a in outcome.attempts],
            outcome_status="recovered",
            next_step_hints=["Report repaired validation and summarize edits"],
            handoff_checkpoint="repair_recovered",
        )))
        if getattr(runner, "_mission_state", None) is not None:
            runner._mission_state.validation_failures = []
            runner._mission_state.last_checkpoint_id = checkpoint.checkpoint_id
            save_mission_state(runner.repo, runner._mission_state)
        return outcome.message

    checkpoint = runner._context_governance.create_checkpoint(inventory, str(getattr(plan, "task_goal", "")), ["repair attempts exhausted"])
    runner.event_callback({"type": "context_checkpoint_created", "checkpoint_id": checkpoint.checkpoint_id, "reason": "repair_exhausted"})
    update_session_state(runner.repo, _with_shell_environment(runner, SessionState(
        affected_files=changed_files,
        validation_plan_summary=[s.step.name for s in result.plan.selected_steps],
        validation_summary="failed",
        last_failed_step=outcome.last_failed_step,
        repair_attempt_summaries=[asdict(a) for a in outcome.attempts],
        outcome_status="failed",
        next_step_hints=["Inspect failing step and rerun with interactive guidance"],
        handoff_checkpoint="repair_exhausted",
    )))
    if getattr(runner, "_mission_state", None) is not None:
        runner._mission_state.validation_failures = [result.failure_summary]
        runner._mission_state.last_failed_summary = outcome.message
        runner._mission_state.last_checkpoint_id = checkpoint.checkpoint_id
        save_mission_state(runner.repo, runner._mission_state)
    return outcome.message


def refresh_live_validation_candidates(
    runner: Any,
    changed_or_created_targets: list[str],
    *,
    observed_commands: list[str] | None = None,
) -> None:
    normalized = {
        _normalize_repo_path(path)
        for path in changed_or_created_targets
        if _normalize_repo_path(path)
    }
    if not normalized:
        return
    current = set(getattr(runner, "_current_verification_targets", set()))
    primary_target = _primary_execution_target(runner)
    if primary_target:
        normalized.add(primary_target)
    combined = current | normalized
    runner._current_verification_targets = combined
    live_suggestions = _suggest_live_validation_commands(combined, list(observed_commands or []))
    if live_suggestions:
        augment_validation_config_with_live_commands(runner.repo, live_suggestions)
