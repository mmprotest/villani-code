from __future__ import annotations
from pathlib import Path
from app.check import run

if __name__ == '__main__':
    root = Path(__file__).resolve().parent  # BUG points at scripts/, not repo root
    print(run(root))
