from __future__ import annotations

from pathlib import Path

import json
import subprocess

from villani_code.benchmark.agents import AGENTS, build_agent_runner
from villani_code.benchmark.agents.aider import AiderAgentRunner
from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner
from villani_code.benchmark.agents.command import CommandAgentRunner
from villani_code.benchmark.agents.opencode import OpenCodeAgentRunner
from villani_code.benchmark.agents.villani import VillaniAgentRunner


def _expected_process_group_kwargs() -> set[tuple[str, object]]:
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        return {("start_new_session", True), ("creationflags", 0), ("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)}
    return {("start_new_session", True), ("creationflags", 0)}


def test_registry_contains_supported_agents() -> None:
    assert AGENTS == {
        "villani": VillaniAgentRunner,
        "aider": AiderAgentRunner,
        "opencode": OpenCodeAgentRunner,
        "claude-code": ClaudeCodeAgentRunner,
    }


def test_dispatcher_builds_named_and_cmd_runners() -> None:
    assert isinstance(build_agent_runner("villani"), VillaniAgentRunner)
    assert isinstance(build_agent_runner("claude-code"), ClaudeCodeAgentRunner)
    assert isinstance(build_agent_runner("cmd:python -c 'print(1)'"), CommandAgentRunner)


def test_aider_command_forwards_model_and_endpoint() -> None:
    runner = AiderAgentRunner()
    cmd = runner.build_command(
        Path("."),
        "fix bug",
        model="qwen-9b",
        base_url="http://127.0.0.1:1234",
        api_key="sk-test",
        provider="openai",
    )
    assert cmd == [
        "aider",
        "--yes",
        "--model",
        "openai/qwen-9b",
        "--openai-api-base",
        "http://127.0.0.1:1234",
        "--openai-api-key",
        "sk-test",
        "--message",
        "fix bug",
    ]


def test_opencode_command_shape_non_windows_with_base_url(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda: "opencode")
    try:
        runner.build_command(
            Path("/tmp/repo"),
            "fix bug",
            model="qwen-9b",
            base_url="http://127.0.0.1:1234",
            api_key="sk-test",
            provider="openai",
        )
    except ValueError as exc:
        message = str(exc)
        assert "provider selection" in message
        assert "base_url passthrough" in message
        assert "model configuration" in message
    else:
        raise AssertionError("expected ValueError when passing unsupported OpenCode provider/base_url/model fields")

    env = runner.build_env(base_url="http://127.0.0.1:1234", api_key="sk-test")
    assert "OPENAI_API_BASE" not in env
    assert "OPENAI_API_KEY" not in env


def test_opencode_command_shape_windows_with_base_url(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda: "opencode.cmd")
    try:
        runner.build_command(
            Path("C:/repo"),
            "fix bug",
            model="qwen-9b",
            base_url="http://127.0.0.1:1234",
            api_key="sk-test",
            provider="openai",
        )
    except ValueError as exc:
        assert "base_url passthrough" in str(exc)
    else:
        raise AssertionError("expected ValueError for unsupported base_url passthrough")


def test_opencode_command_shape_without_base_url(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda: "opencode")
    cmd = runner.build_command(
        Path("/tmp/repo"),
        "fix bug",
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
    )
    assert cmd == ["opencode", "run", "--dir", "/tmp/repo"]


def test_opencode_command_never_uses_unsupported_hostname_flag(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda: "opencode")
    cmd = runner.build_command(
        Path("/tmp/repo"),
        "fix bug",
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
    )
    assert "--hostname" not in cmd
    assert "--attach" not in cmd




def test_opencode_model_provider_overrides_are_rejected_explicitly(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda: "opencode")

    try:
        runner.build_command(
            Path("/tmp/repo"),
            "fix bug",
            model="qwen-9b",
            base_url="http://127.0.0.1:1234",
            api_key="sk-test",
            provider="openai",
        )
    except ValueError as exc:
        msg = str(exc)
        assert "cannot prove backend equivalence" in msg
        assert "api_key passthrough" in msg
    else:
        raise AssertionError("expected ValueError for unsupported backend overrides")


def test_opencode_run_agent_delivers_multiline_prompt_over_stdin_and_writes_artifacts(monkeypatch, tmp_path: Path) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda **kwargs: "opencode.cmd")
    monkeypatch.setattr(runner, "_validate_cli_startup", lambda executable, repo_path: None)

    payload = {}

    class DummyProc:
        pid = 111
        returncode = 0

        def communicate(self, input=None, timeout=None):
            payload["stdin"] = input
            payload["timeout"] = timeout
            return ("Active model: local/test-model\nstdout", "stderr")

        def kill(self):
            payload["killed"] = True

    def fake_popen(command, cwd, stdout, stderr, stdin, env, text, encoding, errors, **kwargs):
        payload["command"] = command
        payload["cwd"] = str(cwd)
        payload["text"] = text
        payload["encoding"] = encoding
        payload["errors"] = errors
        payload["stdin_pipe"] = stdin
        payload["popen_extra"] = kwargs
        return DummyProc()

    monkeypatch.setattr("villani_code.benchmark.agents.opencode.subprocess.Popen", fake_popen)

    prompt = """Benchmark task contract (shared across all agents):

Paragraph one with bullets:
- item 1
- item 2

Paragraph two with Windows path C:\\repo\\file.py
"""
    debug_dir = tmp_path / "debug"
    result = runner.run_agent(
        repo_path=tmp_path,
        prompt=prompt,
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
        timeout=17,
        debug_dir=debug_dir,
    )

    assert result.exit_code == 0
    assert payload["command"] == ["opencode.cmd", "run", "--dir", str(tmp_path)]
    assert payload["stdin"] == prompt
    assert payload["timeout"] == 17
    assert payload["stdin_pipe"] == subprocess.PIPE
    assert payload["encoding"] == "utf-8"
    assert payload["errors"] == "replace"
    assert tuple(payload["popen_extra"].items())[0] in _expected_process_group_kwargs()

    prompt_artifact = Path(result.debug_artifacts["opencode_prompt"])
    assert prompt_artifact.name == "opencode_prompt.txt"
    assert prompt_artifact.read_text(encoding="utf-8") == prompt

    meta_artifact = Path(result.debug_artifacts["opencode_invocation_meta"])
    meta = json.loads(meta_artifact.read_text(encoding="utf-8"))
    assert meta["delivery_mode"] == "stdin"
    assert meta["executable"] == "opencode.cmd"
    assert meta["argv"] == ["opencode.cmd", "run", "--dir", str(tmp_path)]
    assert meta["reported_active_model"] == "local/test-model"


def test_opencode_prompt_artifact_filenames_are_stable() -> None:
    assert OpenCodeAgentRunner.PROMPT_ARTIFACT_FILENAME == "opencode_prompt.txt"
    assert OpenCodeAgentRunner.INVOCATION_META_FILENAME == "opencode_invocation_meta.json"


def test_opencode_startup_failure_message_for_session_not_found() -> None:
    runner = OpenCodeAgentRunner()
    message = runner._startup_failure_message(stderr="Session not found", stdout="")
    assert message is not None
    assert "Session not found" in message


def test_opencode_startup_failure_message_for_usage_output() -> None:
    runner = OpenCodeAgentRunner()
    message = runner._startup_failure_message(stderr="Usage: opencode run [options]", stdout="")
    assert message is not None
    assert "usage/help output" in message


def test_opencode_resolves_cmd_on_windows(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr("villani_code.benchmark.agents.opencode.shutil.which", lambda exe: "C:/npm/opencode.cmd" if exe == "opencode.cmd" else None)
    assert runner._resolve_executable_name(is_windows=True) == "opencode.cmd"


def test_opencode_resolves_plain_binary_on_non_windows(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr("villani_code.benchmark.agents.opencode.shutil.which", lambda exe: "/usr/bin/opencode" if exe == "opencode" else None)
    assert runner._resolve_executable_name(is_windows=False) == "opencode"


def test_opencode_missing_executable_raises_actionable_error(monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr("villani_code.benchmark.agents.opencode.shutil.which", lambda exe: None)
    try:
        runner._resolve_executable_name(is_windows=True)
    except FileNotFoundError as exc:
        message = str(exc)
        assert "opencode.cmd" in message
        assert "Install opencode" in message
    else:
        raise AssertionError("expected FileNotFoundError when opencode executable is missing")


def test_claude_code_command_and_env_forward_model_and_endpoint() -> None:
    runner = ClaudeCodeAgentRunner()
    cmd = runner.build_command(
        Path("."),
        "fix bug",
        model="claude-3-7-sonnet",
        base_url="http://127.0.0.1:8080",
        api_key="sk-ant-test",
        provider="anthropic",
    )
    env = runner.build_env(base_url="http://127.0.0.1:8080", api_key="sk-ant-test")
    assert cmd == [
        "claude",
        "--model",
        "claude-3-7-sonnet",
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "fix bug",
    ]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"


def test_claude_code_command_requires_model() -> None:
    runner = ClaudeCodeAgentRunner()
    try:
        runner.build_command(
            Path("."),
            "fix bug",
            model=None,
            base_url=None,
            api_key=None,
            provider="anthropic",
        )
    except ValueError as exc:
        assert "requires --model" in str(exc)
    else:
        raise AssertionError("expected ValueError when model is missing")


def test_claude_code_prompt_is_final_positional_argument() -> None:
    runner = ClaudeCodeAgentRunner()
    prompt = "edit src/main.py"
    cmd = runner.build_command(
        Path("."),
        prompt,
        model="claude-3-7-sonnet",
        base_url=None,
        api_key=None,
        provider="anthropic",
    )
    assert cmd[-1] == prompt



def test_villani_defaults_provider_to_openai_with_base_url() -> None:
    runner = VillaniAgentRunner()
    cmd = runner.build_command(
        Path("/tmp/repo"),
        "fix bug",
        model="qwen-9b",
        base_url="http://127.0.0.1:1234",
        api_key="sk-test",
        provider=None,
    )
    provider_idx = cmd.index("--provider")
    assert cmd[provider_idx + 1] == "openai"


def test_villani_respects_explicit_provider_override() -> None:
    runner = VillaniAgentRunner()
    cmd = runner.build_command(
        Path("/tmp/repo"),
        "fix bug",
        model="qwen-9b",
        base_url="http://127.0.0.1:1234",
        api_key="sk-test",
        provider="anthropic",
    )
    provider_idx = cmd.index("--provider")
    assert cmd[provider_idx + 1] == "anthropic"


def test_command_runner_appends_prompt() -> None:
    runner = CommandAgentRunner("python -c 'print(1)'")
    cmd = runner.build_command(Path("."), "fix bug", None, None, None, None)
    assert cmd[-1] == "fix bug"


def test_villani_command_keeps_no_stream_and_omits_emit_runtime_events() -> None:
    runner = VillaniAgentRunner()
    cmd = runner.build_command(
        Path('/tmp/repo'),
        'fix bug',
        model='qwen-9b',
        base_url=None,
        api_key=None,
        provider='anthropic',
    )
    assert '--no-stream' in cmd
    assert '--emit-runtime-events' not in cmd


def test_villani_run_agent_missing_runtime_events_file_is_best_effort(monkeypatch) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
    from villani_code.benchmark.models import FieldQuality, TelemetryQuality

    def fake_run_agent(self, repo_path, prompt, model, base_url, api_key, provider, timeout, benchmark_config_json=None, debug_dir=None):
        return AdapterRunResult(
            stdout='ok',
            stderr='',
            exit_code=0,
            timeout=False,
            runtime_seconds=0.1,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map={'num_shell_commands': FieldQuality.INFERRED},
            events=[AdapterEvent(type='command_started', timestamp=1.0, payload={})],
        )

    monkeypatch.setattr('villani_code.benchmark.agents.base.AgentRunner.run_agent', fake_run_agent)

    runner = VillaniAgentRunner()
    result = runner.run_agent(
        repo_path=Path('/tmp'),
        prompt='fix bug',
        model='qwen-9b',
        base_url=None,
        api_key=None,
        provider='anthropic',
        timeout=10,
    )

    assert result.stdout == 'ok'
    assert len(result.events) == 1
    assert result.telemetry_quality == TelemetryQuality.INFERRED


def test_villani_command_includes_benchmark_runtime_json_when_present() -> None:
    runner = VillaniAgentRunner()
    cmd = runner.build_command(
        Path('/tmp/repo'),
        'fix bug',
        model='qwen-9b',
        base_url=None,
        api_key=None,
        provider='anthropic',
        benchmark_config_json='{"enabled":true,"task_id":"t"}',
    )
    assert '--benchmark-runtime-json' in cmd


def test_villani_run_agent_preserves_runtime_event_type(monkeypatch, tmp_path: Path) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
    from villani_code.benchmark.models import FieldQuality, TelemetryQuality

    def fake_run_agent(self, repo_path, prompt, model, base_url, api_key, provider, timeout, benchmark_config_json=None, debug_dir=None):
        return AdapterRunResult(
            stdout='ok',
            stderr='',
            exit_code=0,
            timeout=False,
            runtime_seconds=0.1,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map={'num_shell_commands': FieldQuality.INFERRED},
            events=[AdapterEvent(type='command_started', timestamp=1.0, payload={})],
        )

    monkeypatch.setattr('villani_code.benchmark.agents.base.AgentRunner.run_agent', fake_run_agent)

    events_dir = tmp_path / '.villani_code'
    events_dir.mkdir(parents=True)
    (events_dir / 'runtime_events.jsonl').write_text(
        '{"ts": 10.0, "type": "tool_started", "name": "Read"}\n',
        encoding='utf-8',
    )

    runner = VillaniAgentRunner()
    result = runner.run_agent(
        repo_path=tmp_path,
        prompt='fix bug',
        model='qwen-9b',
        base_url=None,
        api_key=None,
        provider='anthropic',
        timeout=10,
    )

    assert any(event.type == 'tool_started' for event in result.events)


def test_villani_run_agent_extracts_exact_usage_from_transcript(monkeypatch, tmp_path: Path) -> None:
    from villani_code.benchmark.adapters.base import AdapterEvent, AdapterRunResult
    from villani_code.benchmark.models import FieldQuality, TelemetryQuality

    def fake_run_agent(self, repo_path, prompt, model, base_url, api_key, provider, timeout, benchmark_config_json=None, debug_dir=None):
        return AdapterRunResult(
            stdout='ok',
            stderr='',
            exit_code=0,
            timeout=False,
            runtime_seconds=0.1,
            telemetry_quality=TelemetryQuality.INFERRED,
            telemetry_field_quality_map={'num_shell_commands': FieldQuality.INFERRED},
            events=[AdapterEvent(type='command_started', timestamp=1.0, payload={})],
        )

    monkeypatch.setattr('villani_code.benchmark.agents.base.AgentRunner.run_agent', fake_run_agent)

    transcript_dir = tmp_path / '.villani_code' / 'transcripts'
    transcript_dir.mkdir(parents=True)
    (transcript_dir / 'latest.json').write_text(
        json.dumps(
            {
                'responses': [
                    {'usage': {'input_tokens': 10, 'output_tokens': 4}},
                    {'usage': {'input_tokens': 5, 'output_tokens': 1}},
                ]
            }
        ),
        encoding='utf-8',
    )

    runner = VillaniAgentRunner()
    result = runner.run_agent(
        repo_path=tmp_path,
        prompt='fix bug',
        model='qwen-9b',
        base_url=None,
        api_key=None,
        provider='anthropic',
        timeout=10,
    )

    assert result.tokens_input == 15
    assert result.tokens_output == 5
    assert result.total_tokens == 20


def test_agent_runner_uses_utf8_replace_text_mode(monkeypatch, tmp_path: Path) -> None:
    runner = CommandAgentRunner("python -c 'print(1)'")
    payload = {}

    class DummyProc:
        pid = 222
        returncode = 0

        def communicate(self, timeout=None):
            payload["timeout"] = timeout
            # Simulate bytes that cannot be decoded under cp1252.
            return (b"prefix\x81suffix", b"err\x81")

        def kill(self):
            payload["killed"] = True

    def fake_popen(command, cwd, stdout, stderr, env, text, encoding, errors, **kwargs):
        payload["kwargs"] = {
            "text": text,
            "encoding": encoding,
            "errors": errors,
        }
        payload["popen_extra"] = kwargs
        return DummyProc()

    monkeypatch.setattr("villani_code.benchmark.agents.base.subprocess.Popen", fake_popen)

    result = runner.run_agent(
        repo_path=tmp_path,
        prompt="fix bug",
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
        timeout=5,
    )

    assert payload["kwargs"] == {"text": True, "encoding": "utf-8", "errors": "replace"}
    assert tuple(payload["popen_extra"].items())[0] in _expected_process_group_kwargs()
    assert result.stdout == "prefix�suffix"
    assert result.stderr == "err�"
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


def test_agent_runner_normalizes_none_stdout_stderr(monkeypatch, tmp_path: Path) -> None:
    runner = CommandAgentRunner("python -c 'print(1)'")

    class DummyProc:
        pid = 333
        returncode = 0

        def communicate(self, timeout=None):
            return (None, None)

        def kill(self):
            pass

    def fake_popen(command, cwd, stdout, stderr, env, text, encoding, errors, **kwargs):
        return DummyProc()

    monkeypatch.setattr("villani_code.benchmark.agents.base.subprocess.Popen", fake_popen)

    result = runner.run_agent(
        repo_path=tmp_path,
        prompt="fix bug",
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
        timeout=5,
    )

    assert result.stdout == ""
    assert result.stderr == ""
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


def test_agent_runner_timeout_cleanup_uses_bounded_drain(monkeypatch, tmp_path: Path) -> None:
    runner = CommandAgentRunner("python -c 'print(1)'")
    payload = {"communicate_calls": []}

    class DummyProc:
        pid = 444
        returncode = -9

        def communicate(self, input=None, timeout=None):
            payload["communicate_calls"].append(timeout)
            if len(payload["communicate_calls"]) == 1:
                raise subprocess.TimeoutExpired(cmd="cmd", timeout=timeout)
            if timeout is None:
                raise AssertionError("cleanup communicate must be bounded")
            raise subprocess.TimeoutExpired(cmd="cmd", timeout=timeout, output="partial-out", stderr="partial-err")

        def kill(self):
            payload["kill_called"] = True

    monkeypatch.setattr("villani_code.benchmark.agents.base.os.killpg", lambda pgid, sig: payload.setdefault("killpg", []).append((pgid, sig)))
    monkeypatch.setattr("villani_code.benchmark.agents.base.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("villani_code.benchmark.agents.base.os.name", "posix")
    monkeypatch.setattr("villani_code.benchmark.agents.base.subprocess.Popen", lambda *args, **kwargs: DummyProc())

    result = runner.run_agent(
        repo_path=tmp_path,
        prompt="fix bug",
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
        timeout=5,
    )

    assert result.timeout is True
    assert result.exit_code is None
    assert payload["communicate_calls"] == [5, 2]
    assert "bounded drain expired" in result.stderr


def test_opencode_timeout_cleanup_uses_safe_shared_path(monkeypatch, tmp_path: Path) -> None:
    runner = OpenCodeAgentRunner()
    monkeypatch.setattr(runner, "_resolve_executable_name", lambda **kwargs: "opencode")
    monkeypatch.setattr(runner, "_validate_cli_startup", lambda executable, repo_path: None)
    payload = {}

    class DummyProc:
        pid = 555
        returncode = -9

        def communicate(self, input=None, timeout=None):
            payload.setdefault("calls", []).append({"input": input, "timeout": timeout})
            if len(payload["calls"]) == 1:
                raise subprocess.TimeoutExpired(cmd="opencode", timeout=timeout)
            return ("", "")

        def kill(self):
            payload["kill_called"] = True

    monkeypatch.setattr("villani_code.benchmark.agents.base.os.killpg", lambda pgid, sig: None)
    monkeypatch.setattr("villani_code.benchmark.agents.base.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("villani_code.benchmark.agents.base.os.name", "posix")
    monkeypatch.setattr("villani_code.benchmark.agents.base.subprocess.Popen", lambda *args, **kwargs: DummyProc())

    result = runner.run_agent(
        repo_path=tmp_path,
        prompt="hello\nworld",
        model=None,
        base_url=None,
        api_key=None,
        provider=None,
        timeout=9,
    )

    assert result.timeout is True
    assert result.exit_code is None
    assert payload["calls"][0] == {"input": "hello\nworld", "timeout": 9}
    assert payload["calls"][1]["timeout"] == 2


def test_timeout_on_windows_uses_taskkill_tree(monkeypatch) -> None:
    runner = CommandAgentRunner("python -c 'print(1)'")
    payload = {}

    class DummyProc:
        pid = 666
        returncode = -9

        def communicate(self, input=None, timeout=None):
            if timeout == 1:
                raise subprocess.TimeoutExpired(cmd="cmd", timeout=timeout)
            return ("", "")

        def kill(self):
            payload["kill_called"] = True

    monkeypatch.setattr("villani_code.benchmark.agents.base.os.name", "nt")
    monkeypatch.setattr("villani_code.benchmark.agents.base.subprocess.run", lambda args, **kwargs: payload.setdefault("taskkill", args))

    runner._terminate_process_tree(DummyProc(), cleanup_timeout=2)

    assert payload["taskkill"] == ["taskkill", "/PID", "666", "/T", "/F"]
    assert "kill_called" not in payload


def test_timeout_on_posix_uses_process_group_kill(monkeypatch) -> None:
    runner = CommandAgentRunner("python -c 'print(1)'")
    payload = {}

    class DummyProc:
        pid = 777

        def kill(self):
            payload["kill_called"] = True

    monkeypatch.setattr("villani_code.benchmark.agents.base.os.name", "posix")
    monkeypatch.setattr("villani_code.benchmark.agents.base.os.getpgid", lambda pid: pid + 1)
    monkeypatch.setattr("villani_code.benchmark.agents.base.os.killpg", lambda pgid, sig: payload.setdefault("killpg", (pgid, sig)))

    runner._terminate_process_tree(DummyProc(), cleanup_timeout=2)

    assert payload["killpg"][0] == 778
    assert "kill_called" not in payload
