from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass
class ProposedEdit:
    id: str
    diff_text: str
    files_touched: list[str]
    summary: str
    status: str = "pending"


class ProposalStore:
    def __init__(self, root: Path):
        self.root = root
        self._items: dict[str, ProposedEdit] = {}

    def create(self, diff_text: str, files_touched: list[str], summary: str) -> ProposedEdit:
        edit = ProposedEdit(id=uuid4().hex[:8], diff_text=diff_text, files_touched=files_touched, summary=summary)
        self._items[edit.id] = edit
        return edit

    def list(self) -> list[ProposedEdit]:
        return list(self._items.values())

    def get(self, edit_id: str) -> ProposedEdit | None:
        return self._items.get(edit_id)

    def reject(self, edit_id: str) -> bool:
        edit = self._items.get(edit_id)
        if not edit:
            return False
        edit.status = "rejected"
        return True
