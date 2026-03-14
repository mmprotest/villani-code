from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from villani_code.benchmark.agents.claude_code import ClaudeCodeAgentRunner


def run_claude_code_smoke_task(model: str, timeout_seconds: int = 120) -> Path:
    """Run a minimal opt-in Claude Code smoke task in a throwaway git repo."""
    if os.environ.get("RUN_CLAUDE_CODE_SMOKE") != "1":
        raise RuntimeError("Set RUN_CLAUDE_CODE_SMOKE=1 to enable the Claude Code smoke task")
    if shutil.which(ClaudeCodeAgentRunner.CLI_EXECUTABLE) is None:
        raise RuntimeError(f"Missing CLI executable: {ClaudeCodeAgentRunner.CLI_EXECUTABLE}")

    root = Path(tempfile.mkdtemp(prefix="claude-code-smoke-"))
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (repo / "README.md").write_text("smoke\n", encoding="utf-8")

    runner = ClaudeCodeAgentRunner()
    prompt = "Write a file named SENTINEL.txt with the exact content: smoke-ok"
    result = runner.run_agent(
        repo_path=repo,
        prompt=prompt,
        model=model,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        provider="anthropic",
        timeout=timeout_seconds,
        debug_dir=repo / ".smoke_debug",
    )
    if result.timeout:
        raise RuntimeError("Claude Code smoke task timed out")
    if result.exit_code not in {0, None}:
        raise RuntimeError(f"Claude Code smoke task failed with exit_code={result.exit_code}")

    sentinel = repo / "SENTINEL.txt"
    if not sentinel.exists():
        raise RuntimeError("Claude Code smoke task did not create SENTINEL.txt")
    return sentinel
