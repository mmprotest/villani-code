from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


_SERVICE_HINTS = (
    "flask run",
    "uvicorn",
    "gunicorn",
    "streamlit run",
    "python -m http.server",
    "docker compose up",
    "docker-compose up",
)

_SERVICE_EXACT_PATTERNS = (
    re.compile(r"^python\s+[^\s]+\.py$", re.IGNORECASE),
    re.compile(r"^python\s+-m\s+flask\s+run\b", re.IGNORECASE),
    re.compile(r"^flask\s+run\b", re.IGNORECASE),
    re.compile(r"^(npm|pnpm|yarn)\s+(start|run\s+dev|dev)\b", re.IGNORECASE),
)

_PORT_PATTERN = re.compile(r"(?:--port|-p)\s+([0-9]{2,5})")
_BIND_PATTERN = re.compile(r"(?:--host|--bind)\s+([0-9a-zA-Z\.:_-]+)")


@dataclass
class ServiceRecord:
    service_id: str
    command: str
    cwd: str
    started_at: str
    stdout_log_path: str
    stderr_log_path: str
    pid: int | None
    process: subprocess.Popen[str] | None = field(repr=False, default=None)
    base_url: str | None = None
    port: int | None = None
    status: str = "starting"
    readiness: dict[str, Any] = field(default_factory=dict)


def is_likely_service_command(command: str) -> bool:
    compact = " ".join(str(command).strip().split())
    if not compact:
        return False
    lowered = compact.lower()
    if any(token in lowered for token in ("&&", ";", "|", "pytest", "mypy", "ruff", "python -c", "echo ")):
        return False
    if any(hint in lowered for hint in _SERVICE_HINTS):
        return True
    return any(pattern.search(compact) for pattern in _SERVICE_EXACT_PATTERNS)


def infer_service_endpoint(command: str, env: dict[str, str] | None = None, maybe_port_hint: int | None = None) -> tuple[int | None, str | None]:
    command = str(command)
    env = env or {}
    port = maybe_port_hint
    port_match = _PORT_PATTERN.search(command)
    if port_match:
        port = int(port_match.group(1))
    if port is None and env.get("PORT", "").isdigit():
        port = int(env["PORT"])
    if port is None and "http.server" in command:
        tail = command.split("http.server", 1)[1].strip().split()
        if tail and tail[0].isdigit():
            port = int(tail[0])
    host = "127.0.0.1"
    bind_match = _BIND_PATTERN.search(command)
    if bind_match:
        parsed_host = bind_match.group(1)
        if parsed_host and parsed_host not in {"0.0.0.0", "::"}:
            host = parsed_host
    if port is None:
        return None, None
    return port, f"http://{host}:{port}"


def probe_service_readiness(
    *,
    port: int | None,
    base_url: str | None,
    timeout_sec: float = 8.0,
    interval_sec: float = 0.2,
) -> dict[str, Any]:
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    while time.monotonic() - started <= timeout_sec:
        attempt: dict[str, Any] = {"at": time.monotonic(), "tcp_ok": None, "http_ok": None}
        if port is not None:
            try:
                with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                    attempt["tcp_ok"] = True
            except OSError as exc:
                attempt["tcp_ok"] = False
                attempt["tcp_error"] = str(exc)
        if base_url:
            try:
                response = httpx.get(base_url, timeout=0.75)
                attempt["http_ok"] = 200 <= response.status_code < 500
                attempt["http_status"] = int(response.status_code)
            except Exception as exc:  # noqa: BLE001
                attempt["http_ok"] = False
                attempt["http_error"] = str(exc)

        attempts.append(attempt)
        tcp_ready = attempt.get("tcp_ok") in {True, None}
        http_ready = attempt.get("http_ok") in {True, None}
        if tcp_ready and http_ready:
            return {
                "ready": True,
                "method": "tcp+http" if port and base_url else ("tcp" if port else "http"),
                "attempts": attempts,
                "elapsed_sec": round(time.monotonic() - started, 3),
            }
        time.sleep(interval_sec)

    return {
        "ready": False,
        "method": "tcp+http" if port and base_url else ("tcp" if port else "http"),
        "attempts": attempts,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "failure": "service not ready before timeout",
    }


class ServiceManager:
    def __init__(self, repo: Path):
        self.repo = repo
        self._services: dict[str, ServiceRecord] = {}
        self._lock = threading.Lock()

    def list_service_ids(self) -> list[str]:
        with self._lock:
            return list(self._services.keys())

    def list_services(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._to_dict(service) for service in self._services.values()]

    def get_service_status(self, service_id: str) -> dict[str, Any]:
        with self._lock:
            service = self._services.get(service_id)
            if service is None:
                return {"service_id": service_id, "status": "missing", "is_error": True}
            self._refresh_status(service)
            return self._to_dict(service)

    def start_service(
        self,
        command: str,
        cwd: Path,
        env: dict[str, str] | None = None,
        maybe_port_hint: int | None = None,
        readiness_timeout_sec: float = 8.0,
        event_callback: Any | None = None,
    ) -> dict[str, Any]:
        service_id = f"svc-{uuid.uuid4().hex[:10]}"
        logs_dir = self.repo / ".villani_code" / "services"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = logs_dir / f"{service_id}.out.log"
        stderr_path = logs_dir / f"{service_id}.err.log"
        started_at = datetime.now(timezone.utc).isoformat()
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        port, base_url = infer_service_endpoint(command, run_env, maybe_port_hint)
        if callable(event_callback):
            event_callback({"type": "service_launch_requested", "command": command, "cwd": str(cwd), "port": port, "base_url": base_url})

        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            kwargs: dict[str, Any] = {
                "shell": True,
                "cwd": str(cwd),
                "env": run_env,
                "stdout": stdout_handle,
                "stderr": stderr_handle,
                "text": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            else:
                kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)

        record = ServiceRecord(
            service_id=service_id,
            command=command,
            cwd=str(cwd),
            started_at=started_at,
            stdout_log_path=str(stdout_path),
            stderr_log_path=str(stderr_path),
            pid=process.pid,
            process=process,
            base_url=base_url,
            port=port,
        )
        with self._lock:
            self._services[service_id] = record

        if callable(event_callback):
            event_callback({"type": "service_process_spawned", "service_id": service_id, "pid": process.pid})

        readiness = probe_service_readiness(port=port, base_url=base_url, timeout_sec=readiness_timeout_sec)
        record.readiness = readiness
        if process.poll() is not None:
            record.status = "exited"
        else:
            record.status = "running" if readiness.get("ready") else "running_not_ready"

        if callable(event_callback):
            for attempt in readiness.get("attempts", []):
                event_callback({"type": "service_readiness_probe_attempt", "service_id": service_id, **attempt})
            event_callback(
                {
                    "type": "service_readiness_succeeded" if readiness.get("ready") else "service_readiness_failed",
                    "service_id": service_id,
                    "readiness": readiness,
                }
            )

        payload = self._to_dict(record)
        payload["is_error"] = not bool(readiness.get("ready"))
        if payload["is_error"]:
            payload["failure"] = readiness.get("failure", "service readiness failed")
        return payload

    def stop_service(self, service_id: str, event_callback: Any | None = None) -> dict[str, Any]:
        with self._lock:
            service = self._services.get(service_id)
        if service is None:
            return {"service_id": service_id, "status": "missing", "stopped": False, "is_error": True}

        process = service.process
        if process is None or process.poll() is not None:
            service.status = "exited"
            return {**self._to_dict(service), "stopped": False, "already_exited": True}

        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=3)
            stopped = True
        except Exception:  # noqa: BLE001
            stopped = False
            try:
                process.kill()
                process.wait(timeout=2)
                stopped = True
            except Exception:  # noqa: BLE001
                stopped = False
        service.status = "stopped" if stopped else "running"
        if callable(event_callback):
            event_callback({"type": "service_stopped", "service_id": service_id, "stopped": stopped})
        return {**self._to_dict(service), "stopped": stopped, "is_error": not stopped}

    def cleanup_services_started_after(self, baseline: set[str], event_callback: Any | None = None) -> None:
        for service_id in self.list_service_ids():
            if service_id in baseline:
                continue
            self.stop_service(service_id, event_callback=event_callback)

    def _refresh_status(self, service: ServiceRecord) -> None:
        if service.process is None:
            return
        if service.process.poll() is not None and service.status not in {"stopped", "exited"}:
            service.status = "exited"

    def _to_dict(self, service: ServiceRecord) -> dict[str, Any]:
        self._refresh_status(service)
        return {
            "service_id": service.service_id,
            "status": service.status,
            "command": service.command,
            "cwd": service.cwd,
            "started_at": service.started_at,
            "pid": service.pid,
            "base_url": service.base_url,
            "port": service.port,
            "stdout_log_path": service.stdout_log_path,
            "stderr_log_path": service.stderr_log_path,
            "readiness": service.readiness,
        }
