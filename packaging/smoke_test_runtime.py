from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: smoke_test_runtime.py <villani-code-executable>", file=sys.stderr)
        return 2
    executable = Path(sys.argv[1])
    proc = subprocess.Popen(
        [str(executable), "bridge", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write('{"type":"ping","id":"release-smoke"}\n')
    proc.stdin.close()
    lines = [proc.stdout.readline().strip(), proc.stdout.readline().strip()]
    stderr = proc.stderr.read() if proc.stderr is not None else ""
    proc.wait(timeout=15)
    events = [json.loads(line) for line in lines if line]
    if events[:2] != [{"type": "ready", "protocol_version": 1}, {"type": "pong", "id": "release-smoke"}]:
        print(f"unexpected events: {events}; stderr={stderr}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
