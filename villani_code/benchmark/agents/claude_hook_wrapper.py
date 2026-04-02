from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from villani_code.benchmark.agents.claude_hook_logger import append_record, build_record


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        return 2
    events_path = Path(argv[1])
    errors_path = Path(argv[2])
    breadcrumb_path = Path(argv[3])
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _append_line(breadcrumb_path, f"{now} hook_wrapper_start argv={argv[1:]!r}")
    try:
        raw = sys.stdin.read()
        record = build_record(raw)
        append_record(events_path, record)
    except Exception:
        _append_line(errors_path, traceback.format_exc().rstrip())
        return 1

    done = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _append_line(breadcrumb_path, f"{done} hook_wrapper_success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
