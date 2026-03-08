from __future__ import annotations

from pathlib import Path

from villani_code.benchmark.agents import AGENTS, build_agent_runner
from villani_code.benchmark.agents.aider import AiderAgentRunner
from villani_code.benchmark.agents.command import CommandAgentRunner
from villani_code.benchmark.agents.opencode import OpenCodeAgentRunner
from villani_code.benchmark.agents.villani import VillaniAgentRunner


def test_registry_contains_supported_agents() -> None:
    assert AGENTS == {
        "villani": VillaniAgentRunner,
        "aider": AiderAgentRunner,
        "opencode": OpenCodeAgentRunner,
    }


def test_dispatcher_builds_named_and_cmd_runners() -> None:
    assert isinstance(build_agent_runner("villani"), VillaniAgentRunner)
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
    assert cmd == ["opencode", "run", "--model", "openai/qwen-9b", "--prompt", "fix bug"]
    assert env["OPENAI_API_BASE"] == "http://127.0.0.1:1234"
    assert env["OPENAI_API_KEY"] == "sk-test"


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
