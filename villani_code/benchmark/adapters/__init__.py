from villani_code.benchmark.adapters.base import (
    AdapterRunConfig,
    AdapterRunResult,
    AgentAdapter,
    ClaudeCodeAdapter,
    CommandAdapter,
    CopilotCliAdapter,
    OpenCodeAdapter,
    VillaniAdapter,
)


def build_adapter(agent: str) -> AgentAdapter:
    if agent == "villani":
        return VillaniAdapter()
    if agent == "claude":
        return ClaudeCodeAdapter()
    if agent == "opencode":
        return OpenCodeAdapter()
    if agent == "copilot-cli":
        return CopilotCliAdapter()
    if agent.startswith("cmd:"):
        return CommandAdapter(agent.removeprefix("cmd:"))
    if agent.startswith("shell:"):
        return CommandAdapter(agent.removeprefix("shell:"))
    raise ValueError(f"Unsupported agent: {agent}")
