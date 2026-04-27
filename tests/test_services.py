from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from villani_code.services import ServiceManager, is_likely_service_command, probe_service_readiness
from villani_code.tools import execute_tool


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_service_classification_heuristics() -> None:
    assert is_likely_service_command("python app.py")
    assert is_likely_service_command("python -m flask run --port 5000")
    assert is_likely_service_command("npm run dev")
    assert is_likely_service_command("docker compose up")
    assert not is_likely_service_command("python -m pytest -q")
    assert not is_likely_service_command("python -c 'print(1)'")
    assert not is_likely_service_command("echo ok")


def test_readiness_probe_tcp_success() -> None:
    port = _free_port()
    ready = threading.Event()

    def _listener() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            ready.set()
            conn, _addr = sock.accept()
            conn.close()

    thread = threading.Thread(target=_listener, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)

    result = probe_service_readiness(port=port, base_url=None, timeout_sec=2.0, interval_sec=0.05)
    assert result["ready"] is True
    assert result["method"] == "tcp"


def test_service_integration_start_hit_stop(tmp_path: Path) -> None:
    manager = ServiceManager(tmp_path)
    port = _free_port()
    result = manager.start_service(f"python -m http.server {port}", cwd=tmp_path, maybe_port_hint=port, readiness_timeout_sec=6.0)
    assert result["service_id"]
    assert result["pid"]
    assert result["readiness"]["ready"] is True

    import httpx

    response = httpx.get(f"http://127.0.0.1:{port}", timeout=2.0)
    assert response.status_code == 200

    stopped = manager.stop_service(result["service_id"])
    assert stopped["stopped"] is True


def test_persistent_service_command_does_not_block_shell_path(tmp_path: Path) -> None:
    manager = ServiceManager(tmp_path)
    port = _free_port()
    started = time.monotonic()
    result = execute_tool(
        "Bash",
        {"command": f"python -m http.server {port}", "cwd": ".", "timeout_sec": 20},
        tmp_path,
        service_manager=manager,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 4.0
    payload = json.loads(str(result["content"]))
    assert payload["mode"] == "service"
    assert payload["service"]["readiness"]["ready"] is True

    service_id = payload["service"]["service_id"]
    manager.stop_service(service_id)


def test_short_lived_command_uses_normal_execution_path(tmp_path: Path) -> None:
    manager = ServiceManager(tmp_path)
    result = execute_tool(
        "Bash",
        {"command": "python -c \"print('ok')\"", "cwd": ".", "timeout_sec": 10},
        tmp_path,
        service_manager=manager,
    )
    payload = json.loads(str(result["content"]))
    assert payload["mode"] == "command"
    assert payload["exit_code"] == 0
    assert "ok" in payload["stdout"]
