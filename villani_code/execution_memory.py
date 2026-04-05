from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha1
from typing import Any


_MAX_ACTIONS = 16
_MAX_FACTS = 8
_MAX_FILES = 12


@dataclass(slots=True)
class ActionRecord:
    turn_index: int
    tool_name: str
    normalized_action: str
    related_files: tuple[str, ...]
    changed_files_snapshot: tuple[str, ...]
    is_error: bool
    outcome_summary: str
    error_fingerprint: str


@dataclass(slots=True)
class RepeatAssessment:
    matched: bool
    similarity: float
    material_change: bool
    escalation_level: int
    reason: str


@dataclass(slots=True)
class ExecutionMemoryState:
    environment_facts: list[str] = field(default_factory=list)
    recent_actions: deque[ActionRecord] = field(default_factory=lambda: deque(maxlen=_MAX_ACTIONS))
    files_read: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_FILES))
    files_written: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_FILES))
    files_deleted: deque[str] = field(default_factory=lambda: deque(maxlen=_MAX_FILES))
    last_failure_summary: str = ""
    last_failure_action: str = ""
    unfinished_items: list[str] = field(default_factory=list)
    artifact_state: dict[str, Any] = field(default_factory=dict)
    low_information_retries: dict[str, int] = field(default_factory=dict)
    last_repeat_assessment: dict[str, Any] = field(default_factory=dict)


class ExecutionMemory:
    def __init__(self) -> None:
        self.state = ExecutionMemoryState()

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_facts": list(self.state.environment_facts),
            "recent_actions": [
                {
                    "turn_index": item.turn_index,
                    "tool_name": item.tool_name,
                    "normalized_action": item.normalized_action,
                    "related_files": list(item.related_files),
                    "changed_files_snapshot": list(item.changed_files_snapshot),
                    "is_error": item.is_error,
                    "outcome_summary": item.outcome_summary,
                    "error_fingerprint": item.error_fingerprint,
                }
                for item in self.state.recent_actions
            ],
            "files_read": list(self.state.files_read),
            "files_written": list(self.state.files_written),
            "files_deleted": list(self.state.files_deleted),
            "last_failure_summary": self.state.last_failure_summary,
            "last_failure_action": self.state.last_failure_action,
            "unfinished_items": list(self.state.unfinished_items),
            "artifact_state": dict(self.state.artifact_state),
            "low_information_retries": dict(self.state.low_information_retries),
            "last_repeat_assessment": dict(self.state.last_repeat_assessment),
        }

    def register_artifact_state(self, artifact_state: dict[str, Any]) -> None:
        self.state.artifact_state.update(artifact_state)

    def note_unfinished(self, item: str) -> None:
        text = str(item or "").strip()
        if not text:
            return
        if text in self.state.unfinished_items:
            return
        self.state.unfinished_items = [text, *self.state.unfinished_items][:5]

    def clear_unfinished(self) -> None:
        self.state.unfinished_items = []

    def assess_repeat(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        changed_files_now: set[str],
    ) -> RepeatAssessment:
        normalized = normalize_action(tool_name, tool_input)
        candidates = [a for a in reversed(self.state.recent_actions) if a.is_error]
        best: ActionRecord | None = None
        best_score = 0.0
        for item in candidates:
            score = action_similarity(normalized, item.normalized_action)
            if score > best_score:
                best_score = score
                best = item
        if best is None or best_score < 0.8:
            return RepeatAssessment(False, best_score, True, 0, "no_recent_similar_failure")
        changed_since = changed_files_now - set(best.changed_files_snapshot)
        overlap = set(best.related_files).intersection(changed_since)
        material_change = bool(overlap)
        if tool_name == "Bash":
            material_change = material_change or _bash_shape_changed(normalized, best.normalized_action)
        retry_count = int(self.state.low_information_retries.get(best.normalized_action, 0))
        escalation = retry_count + 1 if not material_change else 0
        reason = "similar_failure_with_new_change" if material_change else "similar_failure_without_new_evidence"
        return RepeatAssessment(True, best_score, material_change, escalation, reason)

    def update_from_tool(
        self,
        turn_index: int,
        tool_name: str,
        tool_input: dict[str, Any],
        result: dict[str, Any],
        changed_files_now: set[str],
        repeat_assessment: RepeatAssessment | None = None,
    ) -> None:
        related_files = tuple(_extract_related_files(tool_name, tool_input))
        for path in related_files:
            if tool_name == "Read":
                self._remember_path(self.state.files_read, path)
            elif tool_name in {"Write", "Patch"}:
                self._remember_path(self.state.files_written, path)
        if tool_name == "Bash" and "rm " in str(tool_input.get("command", "")):
            for path in related_files:
                self._remember_path(self.state.files_deleted, path)

        summary = summarize_result(result)
        is_error = bool(result.get("is_error", False))
        normalized = normalize_action(tool_name, tool_input)
        fingerprint = error_fingerprint(result)
        prior_similar_failure = next(
            (
                item
                for item in reversed(self.state.recent_actions)
                if item.is_error and item.normalized_action == normalized
            ),
            None,
        )
        self.state.recent_actions.append(
            ActionRecord(
                turn_index=turn_index,
                tool_name=tool_name,
                normalized_action=normalized,
                related_files=related_files,
                changed_files_snapshot=tuple(sorted(changed_files_now)),
                is_error=is_error,
                outcome_summary=summary,
                error_fingerprint=fingerprint,
            )
        )
        self._extract_environment_facts(tool_name, result)

        if is_error:
            self.state.last_failure_summary = summary
            self.state.last_failure_action = normalized
            self.note_unfinished(summary)
            changed_error_signal = bool(
                prior_similar_failure
                and prior_similar_failure.error_fingerprint
                and prior_similar_failure.error_fingerprint != fingerprint
            )
            if changed_error_signal:
                self.state.low_information_retries.pop(normalized, None)
                self.state.last_repeat_assessment = {
                    **self.state.last_repeat_assessment,
                    "material_change": True,
                    "reason": "similar_failure_but_error_changed",
                    "message": "No strong repeat concern.",
                }
                return
            if repeat_assessment and repeat_assessment.matched and not repeat_assessment.material_change:
                self.state.low_information_retries[normalized] = int(self.state.low_information_retries.get(normalized, 0)) + 1
            else:
                self.state.low_information_retries.pop(normalized, None)
        else:
            self.state.low_information_retries.pop(normalized, None)

    def build_turn_summary(self) -> str:
        facts = "; ".join(self.state.environment_facts[:3]) or "none"
        file_status = []
        if self.state.files_read:
            file_status.append(f"read: {', '.join(list(self.state.files_read)[:3])}")
        if self.state.files_written:
            file_status.append(f"written: {', '.join(list(self.state.files_written)[:3])}")
        artifacts = ", ".join(f"{k}={v}" for k, v in list(self.state.artifact_state.items())[:4]) or "unknown"
        repeat_note = "none"
        if self.state.last_repeat_assessment:
            repeat_note = str(self.state.last_repeat_assessment.get("message", "none"))
        unfinished = "; ".join(self.state.unfinished_items[:2]) or "none"
        return (
            "<execution-memory>\n"
            f"Environment facts: {facts}\n"
            f"Artifact/file state: {'; '.join(file_status) if file_status else 'none'}; artifacts: {artifacts}\n"
            f"Most relevant recent failure: {self.state.last_failure_summary or 'none'}\n"
            f"Repeat-risk signal: {repeat_note}\n"
            f"Still unfinished: {unfinished}\n"
            "Guidance: Ground next move in concrete evidence. Avoid near-identical retries unless code, environment, or error evidence changed. Prefer actions that gather new evidence or test a changed hypothesis.\n"
            "</execution-memory>"
        )

    def _extract_environment_facts(self, tool_name: str, result: dict[str, Any]) -> None:
        text = str(result.get("content", ""))
        if tool_name == "Bash" and not result.get("is_error"):
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                stderr = str(payload.get("stderr", ""))
                stdout = str(payload.get("stdout", ""))
                text = "\n".join([stderr, stdout])
        patterns = [
            r"command not found: ([^\n]+)",
            r"No module named ([^\n]+)",
            r"No such file or directory: ([^\n]+)",
            r"Permission denied[: ]+([^\n]+)",
            r"python([0-9.]*)[: ]+can't open file '([^']+)'",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            fact = re.sub(r"\s+", " ", match.group(0)).strip()
            if fact and fact not in self.state.environment_facts:
                self.state.environment_facts = [fact, *self.state.environment_facts][: _MAX_FACTS]
            break

    @staticmethod
    def _remember_path(target: deque[str], path: str) -> None:
        normalized = str(path).replace("\\", "/").lstrip("./")
        if not normalized:
            return
        existing = [item for item in target if item != normalized]
        target.clear()
        target.extend([normalized, *existing][: target.maxlen or _MAX_FILES])


def summarize_result(result: dict[str, Any], limit: int = 180) -> str:
    text = str(result.get("content", "")).strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def error_fingerprint(result: dict[str, Any]) -> str:
    text = summarize_result(result, limit=300)
    return sha1(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def normalize_action(tool_name: str, tool_input: dict[str, Any]) -> str:
    name = str(tool_name or "").strip()
    if name == "Bash":
        command = str(tool_input.get("command", ""))
        return f"Bash:{normalize_command(command)}"
    if name in {"Read", "Write", "Patch"}:
        path = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
        return f"{name}:{path}"
    stable = ",".join(f"{k}={tool_input[k]}" for k in sorted(tool_input))
    return f"{name}:{stable}"


def normalize_command(command: str) -> str:
    lowered = str(command or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = lowered.replace("python3", "python")
    return lowered


def action_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    l_tokens = set(left.split())
    r_tokens = set(right.split())
    if not l_tokens or not r_tokens:
        return 0.0
    return len(l_tokens.intersection(r_tokens)) / len(l_tokens.union(r_tokens))


def _bash_shape_changed(current: str, previous: str) -> bool:
    return normalize_command(current.split(":", 1)[-1]) != normalize_command(previous.split(":", 1)[-1])


def _extract_related_files(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    if tool_name in {"Read", "Write", "Patch"}:
        path = str(tool_input.get("file_path", "")).replace("\\", "/").lstrip("./")
        return [path] if path else []
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        return [m.group(1).replace("\\", "/").lstrip("./") for m in re.finditer(r"([\w./-]+\.(?:py|js|ts|json|md|toml|yaml|yml))", command)]
    return []
