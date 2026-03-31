from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from villani_code.runtime_paths import get_memory_dir

import yaml


@dataclass
class Skill:
    name: str
    description: str
    prompt: str


def discover_skills(repo: Path) -> dict[str, Skill]:
    roots = [get_memory_dir(repo) / "skills"]
    skills: dict[str, Skill] = {}
    for root in roots:
        if not root.exists():
            continue
        for skill_file in root.rglob("SKILL.md"):
            raw = skill_file.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)
            name = meta.get("name") or skill_file.parent.name
            skills[name] = Skill(name=name, description=meta.get("description", ""), prompt=body)
    return skills


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        _, fm, body = text.split("---\n", 2)
        return (yaml.safe_load(fm) or {}), body
    return {}, text
