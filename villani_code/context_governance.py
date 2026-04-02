from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class ContextInclusionReason(str, Enum):
    TASK_RELEVANCE = "task_relevance"
    PLAN_TARGET = "plan_target"
    MEMORY_SIGNAL = "memory_signal"
    VALIDATION_SIGNAL = "validation_signal"
    REPAIR_SIGNAL = "repair_signal"
    CHECKPOINT_HANDOFF = "checkpoint_handoff"


class ContextExclusionReason(str, Enum):
    IRRELEVANT = "irrelevant"
    BUDGET_PRESSURE = "budget_pressure"
    DUPLICATE = "duplicate"
    STALE = "stale"


class ContextPressureLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    OVERFLOW_RISK = "overflow_risk"


@dataclass(slots=True)
class ContextItem:
    source_id: str
    source_type: str
    summary: str
    estimated_units: int
    pressure_share: float
    included: bool
    included_reason: ContextInclusionReason | None = None
    excluded_reason: ContextExclusionReason | None = None
    why: str = ""


@dataclass(slots=True)
class ContextBudgetEstimate:
    total_units: int
    budget_limit: int
    pressure: float
    pressure_level: ContextPressureLevel


@dataclass(slots=True)
class ContextCompactionResult:
    source_type: str
    original_units: int
    compacted_units: int
    kept_facts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ContextInventory:
    task_id: str
    active_items: list[ContextItem] = field(default_factory=list)
    excluded_items: list[ContextItem] = field(default_factory=list)
    budget: ContextBudgetEstimate | None = None
    compactions: list[ContextCompactionResult] = field(default_factory=list)
    stale_signals: list[str] = field(default_factory=list)
    pruning_events: int = 0


@dataclass(slots=True)
class SessionCheckpoint:
    checkpoint_id: str
    created_at: str
    task_summary: str
    compact_context: ContextInventory
    handoff_notes: list[str] = field(default_factory=list)


class ContextCompactor:
    @staticmethod
    def compact_validation_logs(text: str) -> ContextCompactionResult:
        return _compact_lines("validation_log", text, ("failed", "error", "summary", "status", "step"))

    @staticmethod
    def compact_shell_output(text: str) -> ContextCompactionResult:
        return _compact_lines("shell_output", text, ("error", "failed", "exit", "warning", "traceback"))

    @staticmethod
    def compact_repo_summary(text: str) -> ContextCompactionResult:
        return _compact_lines("repo_summary", text, ("root", "manifest", "language", "tests", "entrypoint"))

    @staticmethod
    def compact_previous_task_state(text: str) -> ContextCompactionResult:
        return _compact_lines("previous_task_state", text, ("task", "outcome", "risk", "validation", "checkpoint"))

    @staticmethod
    def compact_repair_history(text: str) -> ContextCompactionResult:
        return _compact_lines("repair_history", text, ("attempt", "failure", "fix", "status", "step"))

    @staticmethod
    def compact_mission_sections(sections: dict[str, list[str]]) -> list[ContextCompactionResult]:
        results: list[ContextCompactionResult] = []
        for key, values in sections.items():
            text = "\n".join(v for v in values if str(v).strip())
            results.append(_compact_lines(f"mission_{key}", text, ("failed", "error", "summary", "active", "skill", "verified")))
        return results

    @staticmethod
    def build_compact_mission_summary(sections: dict[str, list[str]]) -> str:
        lines: list[str] = []
        for key, values in sections.items():
            clean = [str(v).strip() for v in values if str(v).strip()]
            if not clean:
                continue
            lines.append(f"{key}: {clean[0]}")
            if len(clean) > 1:
                lines.append(f"{key} (+{len(clean)-1} more)")
        return "\n".join(lines[:16])


def _compact_lines(source_type: str, text: str, signal_tokens: tuple[str, ...]) -> ContextCompactionResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    original_units = len(text)
    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.lower()
        if any(token in lowered for token in signal_tokens):
            if line not in seen:
                kept.append(line)
                seen.add(line)
        if len(kept) >= 10:
            break
    if not kept and lines:
        kept.append(lines[0][:220])
    compacted_text = "\n".join(kept)
    return ContextCompactionResult(
        source_type=source_type,
        original_units=original_units,
        compacted_units=len(compacted_text),
        kept_facts=kept,
    )


class ContextGovernanceManager:
    def __init__(self, repo: Path, budget_limit: int = 35_000):
        self.repo = repo.resolve()
        self.path = self.repo / ".villani" / "context_state.json"
        self.checkpoint_root = self.repo / ".villani" / "session_checkpoints"
        self.budget_limit = budget_limit

    def load_inventory(self) -> ContextInventory:
        if not self.path.exists():
            return ContextInventory(task_id="unknown")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return self._from_dict(payload)

    def save_inventory(self, inventory: ContextInventory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._to_dict(inventory), indent=2), encoding="utf-8")

    def register_item(
        self,
        inventory: ContextInventory,
        source_id: str,
        source_type: str,
        summary: str,
        estimated_units: int,
        reason: ContextInclusionReason,
        why: str,
    ) -> None:
        if any(i.source_id == source_id for i in inventory.active_items):
            inventory.excluded_items.append(
                ContextItem(source_id, source_type, summary, estimated_units, 0.0, False, None, ContextExclusionReason.DUPLICATE, "duplicate source")
            )
            return
        inventory.active_items.append(
            ContextItem(source_id, source_type, summary, estimated_units, 0.0, True, reason, None, why)
        )
        self._recompute_budget(inventory)

    def exclude_candidate(
        self,
        inventory: ContextInventory,
        source_id: str,
        source_type: str,
        summary: str,
        estimated_units: int,
        reason: ContextExclusionReason,
        why: str,
    ) -> None:
        inventory.excluded_items.append(
            ContextItem(source_id, source_type, summary, estimated_units, 0.0, False, None, reason, why)
        )

    def prune_for_budget(self, inventory: ContextInventory) -> None:
        self._recompute_budget(inventory)
        while inventory.budget and inventory.budget.pressure_level in {ContextPressureLevel.HIGH, ContextPressureLevel.OVERFLOW_RISK} and len(inventory.active_items) > 1:
            drop = inventory.active_items.pop(0)
            inventory.excluded_items.append(
                ContextItem(drop.source_id, drop.source_type, drop.summary, drop.estimated_units, 0.0, False, None, ContextExclusionReason.BUDGET_PRESSURE, "pruned by budget pressure")
            )
            inventory.pruning_events += 1
            self._recompute_budget(inventory)

    def detect_stale_context(self, inventory: ContextInventory, task_mode: str, repair_attempts: int) -> list[str]:
        signals: list[str] = []
        unrelated = [i for i in inventory.active_items if i.source_type == "file" and task_mode in {"docs_update", "docs_update_safe"} and not i.source_id.endswith(".md")]
        if unrelated and len(unrelated) >= 2:
            signals.append("docs_mode_with_unrelated_code_files")
        if repair_attempts >= 2 and len(inventory.active_items) >= 8:
            signals.append("repeated_repairs_with_bloated_context")
        if len({i.source_type for i in inventory.active_items}) >= 5 and len(inventory.active_items) >= 10:
            signals.append("multi_source_context_drift")
        inventory.stale_signals = signals
        return signals

    def create_checkpoint(self, inventory: ContextInventory, task_summary: str, handoff_notes: list[str]) -> SessionCheckpoint:
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)
        checkpoint_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        compact = ContextInventory(
            task_id=inventory.task_id,
            active_items=inventory.active_items[:8],
            excluded_items=inventory.excluded_items[-12:],
            budget=inventory.budget,
            compactions=inventory.compactions[-10:],
            stale_signals=inventory.stale_signals[:8],
            pruning_events=inventory.pruning_events,
        )
        cp = SessionCheckpoint(
            checkpoint_id=checkpoint_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            task_summary=task_summary[:220],
            compact_context=compact,
            handoff_notes=handoff_notes[:8],
        )
        path = self.checkpoint_root / f"{checkpoint_id}.json"
        path.write_text(json.dumps(self._checkpoint_to_dict(cp), indent=2), encoding="utf-8")
        return cp

    def reset_from_checkpoint(self, checkpoint_id: str) -> SessionCheckpoint:
        path = self.checkpoint_root / f"{checkpoint_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cp = self._checkpoint_from_dict(payload)
        self.save_inventory(cp.compact_context)
        return cp

    def _recompute_budget(self, inventory: ContextInventory) -> None:
        total = sum(item.estimated_units for item in inventory.active_items)
        pressure = total / max(self.budget_limit, 1)
        if pressure < 0.45:
            level = ContextPressureLevel.LOW
        elif pressure < 0.75:
            level = ContextPressureLevel.MODERATE
        elif pressure < 1.0:
            level = ContextPressureLevel.HIGH
        else:
            level = ContextPressureLevel.OVERFLOW_RISK
        inventory.budget = ContextBudgetEstimate(total_units=total, budget_limit=self.budget_limit, pressure=round(pressure, 3), pressure_level=level)
        if total:
            for item in inventory.active_items:
                item.pressure_share = round(item.estimated_units / total, 3)

    def _to_dict(self, inventory: ContextInventory) -> dict[str, Any]:
        return {
            "task_id": inventory.task_id,
            "active_items": [asdict(i) for i in inventory.active_items],
            "excluded_items": [asdict(i) for i in inventory.excluded_items],
            "budget": asdict(inventory.budget) if inventory.budget else None,
            "compactions": [asdict(c) for c in inventory.compactions],
            "stale_signals": inventory.stale_signals,
            "pruning_events": inventory.pruning_events,
        }

    def _from_dict(self, payload: dict[str, Any]) -> ContextInventory:
        budget = payload.get("budget") or None
        return ContextInventory(
            task_id=str(payload.get("task_id", "unknown")),
            active_items=[self._context_item_from_dict(v) for v in payload.get("active_items", [])],
            excluded_items=[self._context_item_from_dict(v) for v in payload.get("excluded_items", [])],
            budget=ContextBudgetEstimate(**budget) if budget else None,
            compactions=[ContextCompactionResult(**v) for v in payload.get("compactions", [])],
            stale_signals=[str(v) for v in payload.get("stale_signals", [])],
            pruning_events=int(payload.get("pruning_events", 0)),
        )

    def _context_item_from_dict(self, payload: dict[str, Any]) -> ContextItem:
        included_reason = payload.get("included_reason")
        excluded_reason = payload.get("excluded_reason")
        return ContextItem(
            source_id=str(payload.get("source_id", "")),
            source_type=str(payload.get("source_type", "")),
            summary=str(payload.get("summary", "")),
            estimated_units=int(payload.get("estimated_units", 0)),
            pressure_share=float(payload.get("pressure_share", 0.0)),
            included=bool(payload.get("included", False)),
            included_reason=ContextInclusionReason(included_reason) if included_reason else None,
            excluded_reason=ContextExclusionReason(excluded_reason) if excluded_reason else None,
            why=str(payload.get("why", "")),
        )

    def _checkpoint_to_dict(self, checkpoint: SessionCheckpoint) -> dict[str, Any]:
        return {
            "checkpoint_id": checkpoint.checkpoint_id,
            "created_at": checkpoint.created_at,
            "task_summary": checkpoint.task_summary,
            "compact_context": self._to_dict(checkpoint.compact_context),
            "handoff_notes": checkpoint.handoff_notes,
        }

    def _checkpoint_from_dict(self, payload: dict[str, Any]) -> SessionCheckpoint:
        return SessionCheckpoint(
            checkpoint_id=str(payload.get("checkpoint_id", "")),
            created_at=str(payload.get("created_at", "")),
            task_summary=str(payload.get("task_summary", "")),
            compact_context=self._from_dict(payload.get("compact_context", {})),
            handoff_notes=[str(v) for v in payload.get("handoff_notes", [])],
        )
