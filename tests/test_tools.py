import json
import os

from villani_code.tools import BashInput, _run_bash, _task_command_env


def test_task_command_env_removes_agent_runtime(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/installed-agent/venv")
    monkeypatch.setenv("PYTHONHOME", "/bad/pythonhome")
    monkeypatch.setenv("PYTHONPATH", "/bad/pythonpath")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            [
                "/usr/local/bin",
                "/installed-agent/venv/bin",
                "/usr/bin",
                "/tmp/not-the-active-venv/bin",
            ]
        ),
    )

    env = _task_command_env()

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONHOME" not in env
    assert "PYTHONPATH" not in env
    assert "/installed-agent/venv/bin" not in env["PATH"].split(os.pathsep)
    assert "/usr/local/bin" in env["PATH"].split(os.pathsep)
    assert "/usr/bin" in env["PATH"].split(os.pathsep)
    assert "/tmp/not-the-active-venv/bin" in env["PATH"].split(os.pathsep)


def test_run_bash_uses_sanitized_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/installed-agent/venv")
    monkeypatch.setenv("PYTHONHOME", "/bad/pythonhome")
    monkeypatch.setenv("PYTHONPATH", "/bad/pythonpath")
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(["/usr/bin", "/installed-agent/venv/bin"]),
    )

    result = _run_bash(
        BashInput(
            command=(
                "/usr/bin/env | /usr/bin/sort"
            ),
            cwd=".",
            timeout_sec=10,
        ),
        tmp_path,
        unsafe=True,
    )

    payload = json.loads(result)

    assert payload["exit_code"] == 0
    assert "VIRTUAL_ENV=" not in payload["stdout"]
    assert "PYTHONHOME=" not in payload["stdout"]
    assert "PYTHONPATH=" not in payload["stdout"]
    assert "/installed-agent/venv/bin" not in payload["stdout"]
    assert "PATH=/usr/bin" in payload["stdout"]
