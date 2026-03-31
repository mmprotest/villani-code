from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from villani_code.runtime_paths import get_memory_dir
from typing import Any

import yaml


@dataclass
class SubagentConfig:
    name: str
    description: str = ""
    model: str | None = None
    allowed_tools: list[str] | None = None
    denied_tools: list[str] | None = None
    max_tokens: int | None = None
    system_prompt: str = ""


def load_subagents(repo: Path) -> dict[str, SubagentConfig]:
    builtins = {
        "Explore": SubagentConfig(name="Explore", description="Read-only exploration", denied_tools=["Write", "Patch"]),
        "Plan": SubagentConfig(name="Plan", description="Planning only", denied_tools=["Write", "Patch", "Bash"]),
        "General": SubagentConfig(name="General", description="General purpose"),
    }
    custom_dir = get_memory_dir(repo) / "agents"
    if custom_dir.exists():
        for p in custom_dir.iterdir():
            if p.suffix not in {".json", ".yaml", ".yml"}:
                continue
            raw: dict[str, Any]
            if p.suffix == ".json":
                raw = json.loads(p.read_text(encoding="utf-8"))
            else:
                raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            cfg = SubagentConfig(
                name=raw["name"],
                description=raw.get("description", ""),
                model=raw.get("model"),
                allowed_tools=raw.get("allowed_tools"),
                denied_tools=raw.get("denied_tools"),
                max_tokens=raw.get("max_tokens"),
                system_prompt=raw.get("system_prompt", ""),
            )
            builtins[cfg.name] = cfg
    return builtins
