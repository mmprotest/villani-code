from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


def now_local_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_path_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def normalize_content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        out: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                out.append(block)
            elif isinstance(block, str):
                out.append({"type": "text", "text": block})
        return out
    return []


def is_effectively_empty_content(blocks: Any) -> bool:
    normalized = normalize_content_blocks(blocks)
    if not normalized:
        return True
    for block in normalized:
        if block.get("type") != "text":
            return False
        if str(block.get("text", "")).strip():
            return False
    return True


def merge_extra_json(payload: dict[str, Any], extra_json: str | None) -> dict[str, Any]:
    if not extra_json:
        return payload
    data = json.loads(extra_json)
    merged = deepcopy(payload)
    for key, value in data.items():
        merged[key] = value
    return merged
