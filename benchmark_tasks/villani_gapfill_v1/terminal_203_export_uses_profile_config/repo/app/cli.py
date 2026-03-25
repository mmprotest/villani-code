from __future__ import annotations
import argparse
from pathlib import Path
from .config import profile_settings

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="default")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    settings = profile_settings("default")
    delimiter = settings.get("delimiter", ",")
    Path(args.output).write_text(f"name{delimiter}score\nA{delimiter}1\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
