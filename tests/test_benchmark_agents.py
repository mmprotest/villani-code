from __future__ import annotations
from pathlib import Path

from villani_code.benchmark.agents import AGENTS, build_agent_runner
from villani_code.benchmark.agents.aider import AiderAgentRunner
from villani_code.benchmark.agents.base import AgentRunner
from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner
from villani_code.benchmark.agents.command import CommandAgentRunner
from villani_code.benchmark.agents.opencode import OpenCodeAgentRunner
from villani_code.benchmark.agents.villani import VillaniAgentRunner


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


def test_opencode_command_with_base_url_uses_local_provider_prefix() -> None:
    runner = OpenCodeAgentRunner()
    cmd = runner.build_command(
        Path("."),
        "fix bug",
        model="qwen-9b",
        base_url="http://127.0.0.1:1234",
        api_key="sk-test",
        provider="openai",
    )
    env = runner.build_env(base_url="http://127.0.0.1:1234", api_key="sk-test")
    assert cmd == [
        "opencode",
        "run",
        "--model",
        "villani-openai-compatible/qwen-9b",
        "--format",
        "json",
        "--dangerously-skip-permissions",
        "fix bug",
    ]
    assert env["OPENAI_API_KEY"] == "sk-test"


def test_opencode_command_without_base_url_preserves_model() -> None:
    runner = OpenCodeAgentRunner()
    cmd = runner.build_command(
        Path("."),
        "fix bug",
        model="qwen-9b",
        base_url=None,
        api_key=None,
        provider="openai",
    )
    assert cmd == [
        "opencode",
        "run",
        "--model",
        "qwen-9b",
        "--format",
        "json",
        "--dangerously-skip-permissions",
        "fix bug",
    ]


def test_opencode_env_sets_openai_api_key_from_api_key() -> None:
    runner = OpenCodeAgentRunner()
    env = runner.build_env(base_url="http://127.0.0.1:1234", api_key="sk-test")
    assert env["OPENAI_API_KEY"] == "sk-test"


def test_opencode_base_url_normalization() -> None:
    runner = OpenCodeAgentRunner()
    assert runner._normalize_base_url("http://127.0.0.1:1234") == "http://127.0.0.1:1234/v1"
    assert runner._normalize_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"


def test_opencode_env_defaults_api_key_to_dummy_when_base_url_present() -> None:
    runner = OpenCodeAgentRunner()
    env = runner.build_env(base_url="http://127.0.0.1:1234", api_key=None)
    assert env["OPENAI_API_KEY"] == "dummy"


def test_resolve_subprocess_command_wraps_windows_cmd_shim(monkeypatch) -> None:
    monkeypatch.setattr("villani_code.benchmark.agents.base.sys.platform", "win32")
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")

    def fake_which(executable: str) -> str | None:
        if executable == "opencode":
            return None
        if executable == "opencode.cmd":
            return "C:\\Users\\me\\AppData\\Roaming\\npm\\opencode.cmd"
        return None

    monkeypatch.setattr("villani_code.benchmark.agents.base.shutil.which", fake_which)
    resolved = AgentRunner._resolve_subprocess_command(["opencode", "run", "fix bug"])
    assert resolved == [
        "C:\\Windows\\System32\\cmd.exe",
        "/d",
        "/c",
        "C:\\Users\\me\\AppData\\Roaming\\npm\\opencode.cmd",
        "run",
        "fix bug",
    ]


def test_opencode_run_agent_writes_project_config_for_base_url(tmp_path: Path, monkeypatch) -> None:
    runner = OpenCodeAgentRunner()
    captured: dict[str, list[str] | dict[str, str]] = {}

    class DummyProc:
        returncode = 0

        def communicate(self, timeout):
            return ("", "")

    def fake_popen(command, cwd, stdout, stderr, text, env):
        captured["command"] = command
        captured["env"] = env
        config_path = Path(cwd) / "opencode.json"
        assert config_path.exists()
        return DummyProc()

    monkeypatch.setattr("villani_code.benchmark.agents.base.subprocess.Popen", fake_popen)
    runner.run_agent(
        repo_path=tmp_path,
        prompt="fix bug",
        model="qwen-9b",
        base_url="http://127.0.0.1:1234",
        api_key=None,
        provider="openai",
        timeout=10,
    )
    assert (tmp_path / "opencode.json").exists() is False
    assert captured["command"] == [
        "opencode",
        "run",
        "--model",
        "villani-openai-compatible/qwen-9b",
        "--format",
        "json",
        "--dangerously-skip-permissions",
        "fix bug",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["OPENAI_API_KEY"] == "dummy"


def test_opencode_run_agent_fails_if_opencode_json_exists(tmp_path: Path) -> None:
    runner = OpenCodeAgentRunner()
    (tmp_path / "opencode.json").write_text("{}", encoding="utf-8")
    try:
        runner.run_agent(
            repo_path=tmp_path,
            prompt="fix bug",
            model="qwen-9b",
            base_url="http://127.0.0.1:1234",
            api_key="sk-test",
            provider="openai",
            timeout=10,
        )
    except RuntimeError as exc:
        assert "cannot safely overwrite existing config" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when opencode.json already exists")

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
    env = runner.build_env(base_url="http://127.0.0.1:8080", api_key="sk-ant-test", provider="anthropic")
    assert cmd == [
        "claude",
        "--model",
        "claude-3-7-sonnet",
        "--bare",
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "fix bug",
    ]
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"


def test_claude_code_openai_provider_env_routes_without_anthropic_keys(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://ambient-anthropic.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic-key")
    runner = ClaudeCodeAgentRunner()

    env = runner.build_env(base_url="http://127.0.0.1:9999", api_key="sk-openai-test", provider="openai")

    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999"
    assert env["OPENAI_API_KEY"] == "sk-openai-test"
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_claude_code_anthropic_provider_env_routes_without_openai_keys(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://ambient-openai.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-key")
    runner = ClaudeCodeAgentRunner()

    env = runner.build_env(base_url="http://127.0.0.1:8080", api_key="sk-ant-test", provider="anthropic")

    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert "OPENAI_BASE_URL" not in env
    assert "OPENAI_API_KEY" not in env


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
