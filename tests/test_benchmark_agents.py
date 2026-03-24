from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents import AGENTS, build_agent_runner
from villani_code.benchmark.agents.aider import AiderAgentRunner
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


def test_opencode_command_and_env_forward_model_and_endpoint() -> None:
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
        "qwen-9b",
        "--hostname",
        "http://127.0.0.1:1234",
        "--command",
        "fix bug",
    ]
    assert env["OPENAI_API_BASE"] == "http://127.0.0.1:1234"
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert "OPENAI_PROVIDER" not in env

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
