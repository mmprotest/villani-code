from __future__ import annotations

import json
import shutil
from pathlib import Path

from villani_code.runtime_paths import get_memory_dir


class PluginManager:
    def __init__(self, repo: Path):
        self.repo = repo
        self.root = get_memory_dir(repo) / "plugins"
        self.root.mkdir(parents=True, exist_ok=True)

    def install(self, src: Path) -> str:
        target = self.root / src.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
        return src.name

    def list(self) -> list[str]:
        return sorted([p.name for p in self.root.iterdir() if p.is_dir()])

    def remove(self, name: str) -> None:
        target = self.root / name
        if target.exists():
            shutil.rmtree(target)

    def manifest(self) -> Path:
        m = self.root / "plugins.json"
        m.write_text(json.dumps({"plugins": self.list()}, indent=2), encoding="utf-8")
        return m
