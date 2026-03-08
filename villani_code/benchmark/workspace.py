from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class WorkspaceManager:
    def __init__(self, keep_workspace: bool = False) -> None:
        self.keep_workspace = keep_workspace

    @contextmanager
    def create(self, source_repo: Path) -> Iterator[Path]:
        root = Path(tempfile.mkdtemp(prefix="villani-bench-"))
        target = root / "repo"
        shutil.copytree(source_repo, target)
        try:
            yield target
        finally:
            if not self.keep_workspace and root.exists():
                shutil.rmtree(root, ignore_errors=True)
