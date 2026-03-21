from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'config' / 'settings.json'

def main() -> int:
    data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    print(data['mode'])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
