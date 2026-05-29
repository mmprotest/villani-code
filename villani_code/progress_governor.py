from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


MAX_INTERVENTIONS_PER_RUN = 2
MAX_GOVERNOR_TOKENS = 260
MIN_TURN_BEFORE_REVIEW = 2
MAX_INSTRUCTION_CHARS = 420

ALLOWED_ACTIONS = {"continue", "redirect", "verify", "stop"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
INJECTABLE_ACTIONS = {"redirect", "verify", "stop"}

SYSTEM_PROMPT = """You are the progress governor for a coding-agent runtime.  Your job is to detect execution drift using only the supplied evidence. You do not solve the coding task. You do not use tools. You do not invent missing facts. You do not propose broad redesigns. You issue a correction only when the evidence clearly supports one.  Return strict JSON only with exactly these keys: action, confidence, failure_mode, evidence, instruction  Allowed actions: continue, redirect, verify, stop  Allowed confidence values: low, medium, high  Action meaning: - continue: the current execution trajectory remains coherent. - redirect: the runner is clearly following an unproductive or contradicted path. - verify: a specific verification action is required before completion or further repair. - stop: completion cannot honestly be claimed and there is no grounded next action.  Only use redirect or stop with high confidence and concrete evidence. Prefer continue when evidence is ambiguous. The instruction must be concise, bounded and immediately actionable. Do not include markdown."""


@dataclass(slots=True)
class ProgressSnapshot:
    trigger: str
    turn_index: int
    workspace_revision: int
    objective: str
    intended_targets: list[str]
    changed_files: list[str]
    consecutive_recon_turns: int
    consecutive_no_edit_turns: int
    pending_verification: str
    last_validation_target: str
    last_validation_summary: str
    validation_repeated_without_new_evidence: bool
    scope_expansion_used: bool
    recent_actions: list[dict[str, Any]]


@dataclass(slots=True)
class ProgressVerdict:
    action: str
    confidence: str
    failure_mode: str
    evidence: str
    instruction: str


def parse_progress_verdict(raw: str) -> ProgressVerdict | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    expected = {"action", "confidence", "failure_mode", "evidence", "instruction"}
    if set(parsed.keys()) != expected:
        return None
    values: dict[str, str] = {}
    for key in expected:
        value = parsed.get(key)
        if not isinstance(value, str):
            return None
        values[key] = value
    if values["action"] not in ALLOWED_ACTIONS:
        return None
    if values["confidence"] not in ALLOWED_CONFIDENCE:
        return None
    return ProgressVerdict(**values)


def render_intervention(verdict: ProgressVerdict) -> str:
    return (
        "<progress_governor>\n"
        f"Trajectory review: {verdict.failure_mode.strip()}\n"
        f"Evidence: {verdict.evidence.strip()}\n"
        f"Required next action: {verdict.instruction.strip()}\n"
        "</progress_governor>"
    )


class ProgressGovernor:
    def __init__(self, runner: Any) -> None:
        self.runner = runner

    def choose_trigger(
        self,
        *,
        turn_index: int,
        workspace_revision: int,
        consecutive_recon_turns: int,
        edited_this_turn: bool,
        changed_files: list[str],
        intended_targets: list[str],
    ) -> str:
        if turn_index < MIN_TURN_BEFORE_REVIEW:
            return ""
        if consecutive_recon_turns >= 3:
            return "command_wandering"
        validation_signature = self._validation_signature(workspace_revision)
        if (
            getattr(self.runner, "_validation_repeated_without_new_evidence", False)
            and validation_signature
            and validation_signature != getattr(self.runner, "_last_reviewed_validation_signature", "")
        ):
            return "repeated_verification_failure"
        if edited_this_turn and self._scope_drift(changed_files, intended_targets):
            return "scope_drift_after_edit"
        return ""

    def completion_trigger(
        self,
        *,
        code_change_oriented: bool,
        meaningful_repo_edit_made: bool,
        workspace_revision: int,
        fresh_passing_verification: bool,
    ) -> str:
        if (
            code_change_oriented
            and meaningful_repo_edit_made
            and workspace_revision > 0
            and not fresh_passing_verification
        ):
            return "completion_without_fresh_verification"
        return ""

    def review(self, snapshot: ProgressSnapshot) -> tuple[ProgressVerdict | None, bool]:
        runner = self.runner
        runner.event_callback(
            {
                "type": "progress_governor_started",
                "trigger": snapshot.trigger,
                "turn_index": snapshot.turn_index,
                "workspace_revision": snapshot.workspace_revision,
            }
        )
        try:
            payload = {
                "model": runner.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(self._snapshot_packet(snapshot), sort_keys=True),
                            }
                        ],
                    }
                ],
                "system": [{"type": "text", "text": SYSTEM_PROMPT}],
                "max_tokens": MAX_GOVERNOR_TOKENS,
                "stream": False,
            }
            response = runner.client.create_message(payload, stream=False)
        except Exception as exc:
            runner.event_callback(
                {
                    "type": "progress_governor_failed",
                    "trigger": snapshot.trigger,
                    "reason": f"call_error:{exc.__class__.__name__}",
                }
            )
            return None, False
        text = self._response_text(response)
        if not text.strip():
            runner.event_callback({"type": "progress_governor_failed", "trigger": snapshot.trigger, "reason": "invalid_json"})
            return None, False
        verdict = parse_progress_verdict(text)
        if verdict is None:
            reason = "invalid_json"
            try:
                json.loads(text)
                reason = "invalid_schema"
            except json.JSONDecodeError:
                pass
            runner.event_callback({"type": "progress_governor_failed", "trigger": snapshot.trigger, "reason": reason})
            return None, False
        injectable, suppress_reason = self._eligible(verdict)
        intervened = injectable
        runner._last_governor_trigger = snapshot.trigger
        runner._last_governor_workspace_revision = snapshot.workspace_revision
        runner._last_governor_verdict = verdict
        if snapshot.trigger == "repeated_verification_failure":
            runner._last_reviewed_validation_signature = self._validation_signature(snapshot.workspace_revision)
        runner.event_callback(
            {
                "type": "progress_governor_verdict",
                "trigger": snapshot.trigger,
                "action": verdict.action,
                "confidence": verdict.confidence,
                "failure_mode": verdict.failure_mode,
                "intervened": intervened,
                "workspace_revision": snapshot.workspace_revision,
            }
        )
        if suppress_reason:
            runner.event_callback(
                {
                    "type": "progress_governor_suppressed",
                    "trigger": snapshot.trigger,
                    "reason": suppress_reason,
                }
            )
        if intervened:
            runner._governor_interventions_used += 1
        return verdict, intervened

    def build_snapshot(
        self,
        *,
        trigger: str,
        turn_index: int,
        workspace_revision: int,
        objective: str,
        intended_targets: list[str],
        changed_files: list[str],
        consecutive_recon_turns: int,
        consecutive_no_edit_turns: int,
        pending_verification: str,
        recent_actions: list[dict[str, Any]],
    ) -> ProgressSnapshot:
        return ProgressSnapshot(
            trigger=trigger,
            turn_index=turn_index,
            workspace_revision=workspace_revision,
            objective=str(objective)[:800],
            intended_targets=list(intended_targets)[:16],
            changed_files=list(changed_files)[:24],
            consecutive_recon_turns=consecutive_recon_turns,
            consecutive_no_edit_turns=consecutive_no_edit_turns,
            pending_verification=str(pending_verification)[:700],
            last_validation_target=str(getattr(self.runner, "_last_validation_target", ""))[:300],
            last_validation_summary=str(getattr(self.runner, "_last_validation_summary", ""))[:500],
            validation_repeated_without_new_evidence=bool(getattr(self.runner, "_validation_repeated_without_new_evidence", False)),
            scope_expansion_used=bool(getattr(self.runner, "_scope_expansion_used", False)),
            recent_actions=self._compact_actions(recent_actions),
        )

    def _eligible(self, verdict: ProgressVerdict) -> tuple[bool, str]:
        if verdict.action not in INJECTABLE_ACTIONS:
            return False, ""
        if verdict.confidence != "high":
            return False, "confidence_not_high"
        if not verdict.evidence.strip() or not verdict.instruction.strip():
            return False, ""
        if len(verdict.instruction) > MAX_INSTRUCTION_CHARS:
            return False, "instruction_too_long"
        if getattr(self.runner, "_governor_interventions_used", 0) >= MAX_INTERVENTIONS_PER_RUN:
            return False, "intervention_cap_reached"
        return True, ""

    def _scope_drift(self, changed_files: list[str], intended_targets: list[str]) -> bool:
        if getattr(self.runner, "_scope_expansion_used", False):
            return False
        intended = {self._norm(p) for p in intended_targets if self._norm(p)}
        if not intended:
            return False
        for path in changed_files:
            norm = self._norm(path)
            if norm and norm not in intended and self._authoritative(norm):
                return True
        return False

    def _authoritative(self, path: str) -> bool:
        try:
            from villani_code.repo_rules import classify_repo_path, is_ignored_repo_path

            return (not is_ignored_repo_path(path)) and classify_repo_path(path) == "authoritative"
        except Exception:
            return bool(path) and not path.startswith((".git/", ".villani", "__pycache__/"))

    def _validation_signature(self, workspace_revision: int) -> str:
        target = str(getattr(self.runner, "_last_validation_target", ""))
        summary = str(getattr(self.runner, "_last_validation_summary", ""))
        if not target and not summary:
            return ""
        return json.dumps(
            {
                "revision": workspace_revision,
                "target": target,
                "summary": summary,
                "artifact": str(getattr(self.runner, "_last_validation_artifact_signature", "")),
            },
            sort_keys=True,
        )

    def _snapshot_packet(self, snapshot: ProgressSnapshot) -> dict[str, Any]:
        packet = asdict(snapshot)
        packet["recent_actions"] = self._compact_actions(packet.get("recent_actions", []))
        return packet

    def _compact_actions(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for action in actions[-8:]:
            if not isinstance(action, dict):
                continue
            compacted.append(
                {
                    "tool": str(action.get("tool", ""))[:80],
                    "target": str(action.get("target", ""))[:180],
                    "outcome": str(action.get("outcome", ""))[:220],
                }
            )
        return compacted

    def _response_text(self, response: Any) -> str:
        blocks = response.get("content", []) if isinstance(response, dict) else []
        return "\n".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )

    def _norm(self, path: str) -> str:
        return str(path or "").replace("\\", "/").strip().lstrip("./")
