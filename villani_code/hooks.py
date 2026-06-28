from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass
class HookResult:
    allow: bool
    reason: str = ""
    modified_input: dict[str, Any] | None = None


class HookRunner:
    def __init__(self, hooks: dict[str, list[dict[str, Any]]] | None = None, timeout_sec: int = 20):
        self.hooks = hooks or {}
        self.timeout_sec = timeout_sec

    def run_event(self, event: str, payload: dict[str, Any]) -> HookResult:
        for hook in self.hooks.get(event, []):
            htype = hook.get("type")
            if htype == "shell":
                result = self._run_shell(hook["command"], payload)
            elif htype == "http":
                result = self._run_http(hook["url"], payload)
            else:
                continue
            if not result.allow:
                return result
            if result.modified_input:
                payload["input"] = result.modified_input
        return HookResult(allow=True)

    def _run_shell(self, command: str, payload: dict[str, Any]) -> HookResult:
        proc = subprocess.run(
            command,
            # Hooks intentionally receive the payload on stdin; this uses a private pipe,
            # not the bridge JSONL stdin.
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            shell=True,
            timeout=self.timeout_sec,
        )
        if proc.returncode != 0:
            return HookResult(allow=False, reason=proc.stderr.strip() or "shell hook blocked action")
        out = proc.stdout.strip()
        if not out:
            return HookResult(allow=True)
        parsed = json.loads(out)
        return HookResult(allow=parsed.get("allow", True), reason=parsed.get("reason", ""), modified_input=parsed.get("input"))

    def _run_http(self, url: str, payload: dict[str, Any]) -> HookResult:
        resp = httpx.post(url, json=payload, timeout=self.timeout_sec)
        obj = resp.json()
        return HookResult(allow=obj.get("allow", True), reason=obj.get("reason", ""), modified_input=obj.get("input"))


def load_hooks(repo: Path) -> dict[str, list[dict[str, Any]]]:
    cfg = repo / ".villani" / "settings.json"
    if not cfg.exists():
        return {}
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return data.get("hooks", {})
