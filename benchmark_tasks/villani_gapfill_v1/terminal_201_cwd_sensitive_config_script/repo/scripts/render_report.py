from __future__ import annotations
import json
from pathlib import Path

def main() -> int:
    config_path = Path("configs/report.json")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    print(data["title"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
