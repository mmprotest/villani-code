from villani_code.benchmark.agents.aider import AiderAgentRunner
from villani_code.benchmark.agents.base import AgentRunner
from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner
from villani_code.benchmark.agents.command import CommandAgentRunner
from villani_code.benchmark.agents.opencode import OpenCodeAgentRunner
from villani_code.benchmark.agents.villani import VillaniAgentRunner

AGENTS = {
    "villani": VillaniAgentRunner,
    "aider": AiderAgentRunner,
    "opencode": OpenCodeAgentRunner,
    "claude-code": ClaudeCodeAgentRunner,
}


def build_agent_runner(agent: str) -> AgentRunner:
    if agent.startswith("cmd:"):
        return CommandAgentRunner(agent.removeprefix("cmd:"))
    if agent.startswith("shell:"):
        return CommandAgentRunner(agent.removeprefix("shell:"))
    try:
        return AGENTS[agent]()
    except KeyError as exc:
        raise ValueError(f"Unsupported agent: {agent}") from exc
