from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any


def _fingerprint(stderr: str, stdout: str) -> str:
    body = (stderr or "")[:3000] + "\n" + (stdout or "")[:1200]
    return hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()[:12]


def run_validation_commands(repo_root: str, commands: list[str], timeout: int = 120) -> list[dict[str, Any]]:
    repo = Path(repo_root)
    results: list[dict[str, Any]] = []
    for cmd in commands:
        proc = subprocess.run(["bash", "-lc", cmd], cwd=repo, capture_output=True, text=True, timeout=timeout)
        normalized = {
            "command": cmd,
            "exit": int(proc.returncode),
            "stdout": (proc.stdout or "")[:4000],
            "stderr": (proc.stderr or "")[:4000],
            "failure_fingerprint": _fingerprint(proc.stderr, proc.stdout) if proc.returncode != 0 else "",
        }
        results.append(normalized)
    seen: dict[str, int] = {}
    for r in results:
        fp = r.get("failure_fingerprint", "")
        if not fp:
            r["repeated_failure"] = False
            continue
        seen[fp] = seen.get(fp, 0) + 1
        r["repeated_failure"] = seen[fp] > 1
    return results
