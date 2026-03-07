from villani_code.benchmark.adapters.base import AgentAdapter, AgentAdapterConfig, AgentRunResult, ValidationResult
from villani_code.benchmark.adapters.villani import VillaniAdapter
from villani_code.benchmark.adapters.claude_code import ClaudeCodeAdapter
from villani_code.benchmark.adapters.opencode import OpenCodeAdapter
from villani_code.benchmark.adapters.copilot_cli import CopilotCLIAdapter

__all__ = [
    "AgentAdapter",
    "AgentAdapterConfig",
    "AgentRunResult",
    "ValidationResult",
    "VillaniAdapter",
    "ClaudeCodeAdapter",
    "OpenCodeAdapter",
    "CopilotCLIAdapter",
]

AVAILABLE_ADAPTERS = {"villani", "claude-code", "opencode", "copilot-cli"}
