from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class WorkspaceManager:
    def __init__(self) -> None:
        self._roots: list[Path] = []

    def create(self, source_repo: Path) -> Path:
        root = Path(tempfile.mkdtemp(prefix="villani-bench-"))
        target = root / "repo"
        shutil.copytree(source_repo, target)
        self._roots.append(root)
        return target

    def cleanup(self) -> None:
        for root in self._roots:
            if root.exists():
                shutil.rmtree(root)
        self._roots.clear()
