from __future__ import annotations
import json
from pathlib import Path
from app.config import default_config_path

def run(project_root: Path) -> str:
    path = default_config_path(project_root)
    data = json.loads(path.read_text())
    return f"mode={data['mode']}"
