from pathlib import Path

from villani_code.context_governance import (
    ContextCompactor,
    ContextExclusionReason,
    ContextGovernanceManager,
    ContextInclusionReason,
    ContextInventory,
)
from villani_code.runtime_paths import get_memory_dir


def test_active_context_inventory_and_pruning(tmp_path: Path) -> None:
    manager = ContextGovernanceManager(tmp_path, budget_limit=100)
    inventory = ContextInventory(task_id="task")
    manager.register_item(inventory, "a.py", "file", "a", 80, ContextInclusionReason.TASK_RELEVANCE, "needed")
    manager.register_item(inventory, "b.py", "file", "b", 80, ContextInclusionReason.PLAN_TARGET, "needed")
    manager.prune_for_budget(inventory)
    assert inventory.active_items
    assert inventory.excluded_items
    assert any(item.excluded_reason == ContextExclusionReason.BUDGET_PRESSURE for item in inventory.excluded_items)


def test_compaction_determinism() -> None:
    text = "step one\nFAILED test_x\nFAILED test_x\nsummary: fail\n"
    first = ContextCompactor.compact_validation_logs(text)
    second = ContextCompactor.compact_validation_logs(text)
    assert first.kept_facts == second.kept_facts
    assert first.compacted_units == second.compacted_units


def test_stale_detection_checkpoint_and_reset(tmp_path: Path) -> None:
    manager = ContextGovernanceManager(tmp_path)
    inventory = ContextInventory(task_id="docs")
    for idx in range(3):
        manager.register_item(inventory, f"src/f{idx}.py", "file", "code", 1000, ContextInclusionReason.TASK_RELEVANCE, "old")
    stale = manager.detect_stale_context(inventory, "docs_update_safe", 2)
    assert stale
    checkpoint = manager.create_checkpoint(inventory, "task", ["handoff"])
    payload = (get_memory_dir(tmp_path) / "session_checkpoints" / f"{checkpoint.checkpoint_id}.json").read_text(encoding="utf-8")
    assert "transcript" not in payload.lower()
    restored = manager.reset_from_checkpoint(checkpoint.checkpoint_id)
    assert restored.checkpoint_id == checkpoint.checkpoint_id
