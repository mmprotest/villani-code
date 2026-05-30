from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import TextIO


def read_line_with_timeout(stdout: TextIO, timeout: float = 15) -> str:
    lines: queue.Queue[str | BaseException] = queue.Queue()

    def read_line() -> None:
        try:
            lines.put(stdout.readline())
        except BaseException as exc:  # noqa: BLE001
            lines.put(exc)

    threading.Thread(target=read_line, daemon=True).start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"timed out waiting for runtime output after {timeout} seconds") from exc
    if isinstance(line, BaseException):
        raise line
    if line == "":
        raise EOFError("runtime stdout closed before expected response")
    return line.strip()


def cleanup(proc: subprocess.Popen[str]) -> str:
    if proc.stdin is not None and not proc.stdin.closed:
        proc.stdin.close()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return proc.stderr.read() if proc.stderr is not None else ""


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
        bufsize=1,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    events: list[dict[str, object]] = []
    stderr = ""
    try:
        events.append(json.loads(read_line_with_timeout(proc.stdout)))
        proc.stdin.write('{"type":"ping","id":"release-smoke"}\n')
        proc.stdin.flush()
        events.append(json.loads(read_line_with_timeout(proc.stdout)))
        if proc.poll() is not None:
            raise RuntimeError(f"runtime exited before stdin closed with code {proc.returncode}")
    except Exception as exc:  # noqa: BLE001
        stderr = cleanup(proc)
        print(f"runtime smoke test failed: {exc}; events={events}; stderr={stderr}", file=sys.stderr)
        return 1
    stderr = cleanup(proc)
    if events[:2] != [{"type": "ready", "protocol_version": 1}, {"type": "pong", "id": "release-smoke"}]:
        print(f"unexpected events: {events}; stderr={stderr}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
